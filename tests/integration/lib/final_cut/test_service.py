"""成片服务：在真实临时项目上用随包 ffmpeg 渲染现场合成的低分辨率素材，登记为产物并按时效判定。"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_currency import active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactStatus
from lib.artifacts.version_manager import VersionManager
from lib.bgm.service import BgmLibraryService
from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.edit_timeline.model import BgmClip, EditTimelineContent, TimelineRevision
from lib.edit_timeline.operations import (
    InsertBgm,
    SetReason,
    SetTransition,
    SetTrim,
    SetVolume,
    TransitionSpec,
    TrimSpec,
)
from lib.edit_timeline.store import EditTimelineStore
from lib.final_cut.basis import FinalCutVariant, final_cut_key
from lib.final_cut.errors import FinalCutError
from lib.final_cut.service import FinalCutService
from lib.infra.ffmpeg import ffmpeg_executable
from lib.infra.media_probe import probe_media
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import resource_relative_path
from tests.factories import install_current_video, make_test_clip, wav_bytes

CREATOR = RevisionAuthor(kind="creator", user_id="u1")
VARIANT = FinalCutVariant()
PLAIN: dict[str, Any] = {"narration": VARIANT.narration, "subtitles": VARIANT.subtitles}
"""现场合成的素材没有呈现模型，这里只渲染不带旁白、不烧入字幕的版本。"""


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

    result = await FinalCutService(render_project).render("demo", timeline_id, **PLAIN)

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

    result = await FinalCutService(render_project).render("demo", timeline_id, **PLAIN)

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

    result = await FinalCutService(render_project).render("demo", timeline_id, revision=1, **PLAIN)

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

    result = await FinalCutService(render_project, spawn=spawn_after_edit).render("demo", timeline_id, **PLAIN)

    assert edited.is_set()
    assert result.revision == 1
    assert _status(render_project, timeline_id, result.artifact_path) is ArtifactStatus.STALE


@pytest.mark.usefixtures("media")
async def test_rendering_reads_the_snapshotted_video_version_when_the_formal_file_is_replaced(
    render_project: ProjectManager, tmp_path: Path
) -> None:
    timeline_id = await _create_timeline(render_project)
    readout = await EditTimelineService(render_project).read("demo", timeline_id)
    formal = render_project.get_project_path("demo") / resource_relative_path("reference_videos", "E1U2")
    replacement = tmp_path / "replacement.mp4"
    make_test_clip(replacement, size="90x160", fps=25, seconds=3.0, tone=False)
    replaced = asyncio.Event()

    async def spawn_after_replacing(*args: Any, **kwargs: Any) -> Any:
        if not replaced.is_set():
            os.replace(replacement, formal)
            replaced.set()
        return await asyncio.create_subprocess_exec(*args, **kwargs)

    result = await FinalCutService(render_project, spawn=spawn_after_replacing).render("demo", timeline_id, **PLAIN)

    assert replaced.is_set()
    assert result.acceptance.video_duration == pytest.approx(readout.duration, abs=0.05)
    # 依据描述渲染实际读取的快照；版本记录仍指向同一版本，成片读为 current。
    assert _status(render_project, timeline_id, result.artifact_path) is ArtifactStatus.CURRENT


@pytest.mark.usefixtures("media")
async def test_provider_audio_recorded_as_not_generated_is_left_out_of_the_mix(
    render_project: ProjectManager, tmp_path: Path
) -> None:
    project_dir = render_project.get_project_path("demo")
    silent_request = tmp_path / "silent-request.mp4"
    make_test_clip(silent_request, size="160x90", fps=24, seconds=1.0, tone=True)
    VersionManager(project_dir).add_version(
        "reference_videos", "E1U1", "prompt", source_file=silent_request, execution_generate_audio=False
    )
    shutil.copy2(silent_request, project_dir / resource_relative_path("reference_videos", "E1U1"))
    timeline_id = await _create_timeline(render_project)
    mixed_inputs: list[str] = []

    async def spawn_recording_mix(*args: Any, **kwargs: Any) -> Any:
        if any("anullsrc" in str(arg) for arg in args):
            mixed_inputs.extend(str(args[index + 1]) for index, arg in enumerate(args) if arg == "-i")
        return await asyncio.create_subprocess_exec(*args, **kwargs)

    await FinalCutService(render_project, spawn=spawn_recording_mix).render("demo", timeline_id, **PLAIN)

    # 只有 E1U3 的原声进入混音；E1U1 的快照虽带音轨，版本记录为未生成原声。
    sources = [Path(path).name for path in mixed_inputs if not path.startswith("anullsrc")]
    assert len(sources) == 1
    assert sources[0].startswith("E1U3")


@pytest.mark.usefixtures("media")
async def test_rendering_again_keeps_one_file_and_advances_the_version(render_project: ProjectManager) -> None:
    timeline_id = await _create_timeline(render_project)
    service = FinalCutService(render_project)

    first = await service.render("demo", timeline_id, **PLAIN)
    second = await service.render("demo", timeline_id, **PLAIN)

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
        await FinalCutService(render_project).render("demo", timeline_id, **PLAIN)

    assert caught.value.code == "final_cut_blocked"
    assert [issue["unit_id"] for issue in caught.value.params["issues"]] == ["E1U2"]
    assert not (render_project.get_project_path("demo") / "renders").exists()


@pytest.mark.usefixtures("media")
async def test_transitions_render_without_changing_the_timeline_duration(render_project: ProjectManager) -> None:
    timeline_id = await _create_timeline(render_project)
    edited = await EditTimelineService(render_project).edit(
        "demo",
        timeline_id,
        base_revision=1,
        summary="加转场",
        operations=[
            SetTrim(op="set_trim", clip="c2", trim=TrimSpec(source_in=0.3, source_out=1.2)),
            SetTransition(op="set_transition", clip="c1", transition=TransitionSpec(type="dissolve", duration=0.4)),
            SetTransition(op="set_transition", clip="c2", transition=TransitionSpec(type="fade_black", duration=0.3)),
        ],
        author=CREATOR,
    )

    result = await FinalCutService(render_project).render("demo", timeline_id, **PLAIN)

    # 1.0 + 0.9（截取）+ 0.7：叠化借帧、闪黑淡出淡入都不改变总时长。
    assert edited.duration == pytest.approx(2.6)
    assert result.acceptance.expected_duration == pytest.approx(edited.duration, abs=0.034)
    assert result.acceptance.video_duration == pytest.approx(edited.duration, abs=0.05)
    assert result.acceptance.audio_duration == pytest.approx(edited.duration, abs=0.05)
    assert _status(render_project, timeline_id, result.artifact_path) is ArtifactStatus.CURRENT


def _max_volume_db(path: Path, *, start: float, seconds: float) -> float:
    """成片在 ``start`` 起 ``seconds`` 秒内音频的峰值电平（dB）。"""
    completed = subprocess.run(
        [
            ffmpeg_executable(),
            *("-hide_banner", "-nostdin", "-ss", str(start), "-t", str(seconds), "-i", str(path)),
            *("-vn", "-af", "volumedetect", "-f", "null", "-"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    match = re.search(r"max_volume: (-?[0-9.]+|-inf) dB", completed.stderr)
    assert match is not None, completed.stderr
    return float(match.group(1))


@pytest.mark.usefixtures("media")
async def test_bgm_is_mixed_in_and_cut_at_the_timeline_end_without_changing_the_duration(
    render_project: ProjectManager,
) -> None:
    track = await BgmLibraryService(render_project).upload(
        "demo", filename="theme.wav", content=wav_bytes(3.0, tone_hz=330)
    )
    timeline_id = await _create_timeline(render_project)
    editor = EditTimelineService(render_project)
    # E1U2 那段（1.0–2.5 秒）没有原声；BGM 从 1.0 秒起放 3 秒，越过 3.2 秒的时间线末尾。
    await editor.edit(
        "demo",
        timeline_id,
        base_revision=1,
        summary="加 BGM",
        operations=[InsertBgm(op="insert_bgm", bgm_id=track.id, start=1.0, volume=1.0)],
        author=CREATOR,
    )
    readout = await editor.read("demo", timeline_id)

    result = await FinalCutService(render_project).render("demo", timeline_id, **PLAIN)

    output = render_project.get_project_path("demo") / result.artifact_path
    probe = await probe_media(output)
    video, audio = probe.first_stream("video"), probe.first_stream("audio")
    assert video is not None
    assert audio is not None
    assert video.duration_seconds is not None
    assert audio.duration_seconds is not None
    assert video.duration_seconds == pytest.approx(readout.duration, abs=0.05)
    assert audio.duration_seconds == pytest.approx(video.duration_seconds, abs=0.05)
    assert _max_volume_db(output, start=1.6, seconds=0.6) > -30
    assert _status(render_project, timeline_id, result.artifact_path) is ArtifactStatus.CURRENT


@pytest.mark.usefixtures("media")
async def test_a_bgm_missing_from_the_project_blocks_the_final_cut(render_project: ProjectManager) -> None:
    timeline_id = await _create_timeline(render_project)
    store = EditTimelineStore(render_project, "demo")
    latest = store.find(timeline_id).latest
    content = latest.content.model_copy(
        update={"bgm": (BgmClip(id="b1", bgm_id="bgm-0000abcd", start_us=0, in_us=0, out_us=1_000_000),)}
    )
    revision = TimelineRevision(
        number=latest.number + 1,
        parent=latest.number,
        author=CREATOR,
        summary="加 BGM",
        created_at=datetime.now(UTC).isoformat(),
        content=content,
    )
    document = store.find(timeline_id)
    with store.locked_episode(document.episode):
        store.write(document.model_copy(update={"next_bgm_number": 2, "revisions": (*document.revisions, revision)}))

    with pytest.raises(FinalCutError) as caught:
        await FinalCutService(render_project).check("demo", timeline_id)

    assert caught.value.code == "final_cut_blocked"
