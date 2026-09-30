"""成片服务：在真实临时项目上用随包 ffmpeg 渲染现场合成的低分辨率素材，登记为产物并按时效判定。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_currency import active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactStatus
from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.edit_timeline.model import EditTimelineContent, TimelineRevision
from lib.edit_timeline.operations import SetReason, SetVolume
from lib.edit_timeline.store import EditTimelineStore
from lib.final_cut.basis import FinalCutVariant, final_cut_key
from lib.final_cut.errors import FinalCutError
from lib.final_cut.service import FinalCutService
from lib.infra.media_probe import probe_media
from lib.project.project_manager import ProjectManager
from tests.factories import install_current_video, make_test_clip

CREATOR = RevisionAuthor(kind="creator", user_id="u1")
VARIANT = FinalCutVariant()


def _unit(unit_id: str, text: str) -> dict[str, Any]:
    return {"unit_id": unit_id, "text": text, "duration_seconds": 4}


@pytest.fixture
def render_project(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(str(tmp_path / "projects"))
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.upsert_assets("demo", "characters", {"角色A": {"description": "主角"}})
    manager.update_project(
        "demo", lambda project: project.update({"generation_mode": "reference_video", "aspect_ratio": "9:16"})
    )
    manager.save_script(
        "demo",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "summary": "摘要",
            "novel": {"title": "小说", "chapter": "第一章"},
            "video_units": [
                _unit("E1U1", "@[角色A]{你好}"),
                _unit("E1U2", "{风起了}"),
                _unit("E1U3", "推门进屋"),
            ],
        },
        "episode_1.json",
    )
    return manager


def _install_video(render_project: ProjectManager, tmp_path: Path, unit_id: str, **clip: Any) -> None:
    source = tmp_path / "media" / f"{unit_id}-{len(list((tmp_path / 'media').glob('*.mp4')))}.mp4"
    make_test_clip(source, **clip)
    install_current_video(render_project.get_project_path("demo"), "reference_videos", unit_id, source)


@pytest.fixture
def media(render_project: ProjectManager, tmp_path: Path) -> None:
    _install_video(render_project, tmp_path, "E1U1", size="160x90", fps=24, seconds=1.0, tone=True)
    _install_video(render_project, tmp_path, "E1U2", size="90x160", fps=25, seconds=1.5, tone=False)
    _install_video(render_project, tmp_path, "E1U3", size="160x90", fps=30, seconds=0.7, tone=True)


def _append_revision(render_project: ProjectManager, timeline_id: str, edit: dict[str, dict[str, Any]]) -> int:
    """以最新修订为父修订追加一个修订，按片段 ID 覆盖字段。"""
    store = EditTimelineStore(render_project, "demo")
    document = store.find(timeline_id)
    latest = document.latest
    clips = [{**clip.model_dump(), **edit.get(clip.id, {})} for clip in latest.content.clips]
    revision = TimelineRevision(
        number=latest.number + 1,
        parent=latest.number,
        author=CREATOR,
        summary="测试剪辑",
        created_at=datetime.now(UTC).isoformat(),
        content=EditTimelineContent.model_validate({"clips": clips, "bgm": ()}),
    )
    with store.locked_episode(document.episode):
        store.write(document.model_copy(update={"revisions": (*document.revisions, revision)}))
    return revision.number


async def _create_timeline(render_project: ProjectManager) -> str:
    readout = await EditTimelineService(render_project).create_from_script(
        "demo", episode=1, name="完整版", author=CREATOR
    )
    return readout.timeline.id


def _status(render_project: ProjectManager, timeline_id: str, artifact_path: str) -> ArtifactStatus:
    project_dir = render_project.get_project_path("demo")
    resolver = active_artifact_currency_resolver(project_dir, render_project.load_project("demo"))
    return resolver.compare(final_cut_key(1, timeline_id, VARIANT), artifact_path=artifact_path).status


@pytest.mark.usefixtures("media")
async def test_mechanical_timeline_renders_to_a_current_final_cut_with_aligned_streams(
    render_project: ProjectManager,
) -> None:
    timeline_id = await _create_timeline(render_project)
    readout = await EditTimelineService(render_project).read("demo", timeline_id)

    result = await FinalCutService(render_project).render("demo", timeline_id)

    output = render_project.get_project_path("demo") / result.artifact_path
    probe = await probe_media(output)
    video, audio = probe.first_stream("video"), probe.first_stream("audio")
    assert video is not None
    assert audio is not None
    assert video.duration_seconds is not None
    assert audio.duration_seconds is not None
    assert video.duration_seconds == pytest.approx(readout.duration, abs=0.05)
    assert audio.duration_seconds == pytest.approx(video.duration_seconds, abs=0.05)
    assert result.revision == 1
    assert result.version == 1
    assert _status(render_project, timeline_id, result.artifact_path) is ArtifactStatus.CURRENT


@pytest.mark.usefixtures("media")
async def test_trim_and_hold_shape_the_rendered_duration(render_project: ProjectManager) -> None:
    timeline_id = await _create_timeline(render_project)
    _append_revision(
        render_project,
        timeline_id,
        {
            "c1": {"trim": {"in_us": 200_000, "out_us": 800_000, "basis_version": 1}},
            "c3": {"hold_us": 500_000, "source_volume": 0.5},
        },
    )

    result = await FinalCutService(render_project).render("demo", timeline_id)

    # 0.6（截取）+ 1.5 + 0.7 + 0.5（定格延长）
    assert result.acceptance.expected_duration == pytest.approx(3.3, abs=0.034)
    assert result.acceptance.video_duration == pytest.approx(3.3, abs=0.05)
    assert result.acceptance.audio_duration == pytest.approx(3.3, abs=0.05)
    assert result.revision == 2


@pytest.mark.usefixtures("media")
async def test_rendering_an_older_revision_reads_stale(render_project: ProjectManager) -> None:
    timeline_id = await _create_timeline(render_project)
    editor = EditTimelineService(render_project)
    for revision, volume in ((1, 0.2), (2, 1.0)):
        await editor.edit(
            "demo",
            timeline_id,
            base_revision=revision,
            summary="调整音量",
            operations=[SetVolume(op="set_volume", clip="c2", volume=volume)],
            author=CREATOR,
        )

    result = await FinalCutService(render_project).render("demo", timeline_id, revision=1)

    assert result.revision == 1
    assert _status(render_project, timeline_id, result.artifact_path) is ArtifactStatus.STALE


@pytest.mark.usefixtures("media")
async def test_an_edit_while_rendering_makes_the_final_cut_stale_on_arrival(render_project: ProjectManager) -> None:
    timeline_id = await _create_timeline(render_project)
    edited = asyncio.Event()

    async def spawn_after_edit(*args: Any, **kwargs: Any) -> Any:
        if not edited.is_set():
            await EditTimelineService(render_project).edit(
                "demo",
                timeline_id,
                base_revision=1,
                summary="补充理由",
                operations=[SetReason(op="set_reason", clip="c1", reason="保留开场")],
                author=CREATOR,
            )
            edited.set()
        return await asyncio.create_subprocess_exec(*args, **kwargs)

    result = await FinalCutService(render_project, spawn=spawn_after_edit).render("demo", timeline_id)

    assert edited.is_set()
    assert result.revision == 1
    assert _status(render_project, timeline_id, result.artifact_path) is ArtifactStatus.STALE


@pytest.mark.usefixtures("media")
async def test_rendering_again_keeps_one_file_and_advances_the_version(render_project: ProjectManager) -> None:
    timeline_id = await _create_timeline(render_project)
    service = FinalCutService(render_project)

    first = await service.render("demo", timeline_id)
    second = await service.render("demo", timeline_id)

    assert (first.version, second.version) == (1, 2)
    assert first.artifact_path == second.artifact_path
    render_dir = (render_project.get_project_path("demo") / second.artifact_path).parent
    assert [path.suffix for path in sorted(render_dir.iterdir()) if path.suffix == ".mp4"] == [".mp4"]
    assert not [path for path in render_dir.iterdir() if path.name.startswith(".")]


async def test_a_unit_without_usable_video_blocks_rendering(render_project: ProjectManager, tmp_path: Path) -> None:
    _install_video(render_project, tmp_path, "E1U1", size="160x90", fps=24, seconds=1.0, tone=True)
    _install_video(render_project, tmp_path, "E1U3", size="160x90", fps=30, seconds=0.7, tone=True)
    timeline_id = await _create_timeline(render_project)

    with pytest.raises(FinalCutError) as caught:
        await FinalCutService(render_project).render("demo", timeline_id)

    assert caught.value.code == "final_cut_blocked"
    assert [issue["unit_id"] for issue in caught.value.params["issues"]] == ["E1U2"]
    assert not (render_project.get_project_path("demo") / "renders").exists()


@pytest.mark.usefixtures("media")
async def test_transitions_are_refused_until_they_can_be_rendered(render_project: ProjectManager) -> None:
    timeline_id = await _create_timeline(render_project)
    _append_revision(
        render_project, timeline_id, {"c1": {"transition_to_next": {"type": "dissolve", "duration_us": 400_000}}}
    )

    with pytest.raises(FinalCutError) as caught:
        await FinalCutService(render_project).check("demo", timeline_id)

    assert caught.value.code == "final_cut_content_unsupported"
    assert caught.value.params["clip_ids"] == ["c1"]
