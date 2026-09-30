"""由剪辑时间线生成剪映草稿：字段级映射、下载时代入本机目录与剪映版本、产物时效、登记版本与导出前阻断。"""

from __future__ import annotations

import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_activation import reconcile_artifact_target_claims
from lib.artifacts.artifact_manifest import (
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifest,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifacts.version_manager import VersionManager
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.artifacts.visual_artifact_provenance import build_storyboard_video_artifact_visual_basis
from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.edit_timeline.model import ClipTrim, EditTimelineContent, TimelineRevision
from lib.edit_timeline.operations import SetReason, SetVolume
from lib.edit_timeline.store import EditTimelineStore
from lib.jianying_draft.errors import JianyingDraftError
from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.speech.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis, canonical_narration_text
from lib.speech.speech_artifact_provenance import build_video_duration_basis, build_video_speech_basis
from lib.speech.speech_composition import admit_script_unit
from server.services.presentation.timeline_jianying_draft import TimelineJianyingDraftService
from tests.factories import make_test_video, wav_bytes

CREATOR = RevisionAuthor(kind="creator", user_id="u1")
SETTINGS = TtsSynthesisSettings("openai", "tts-1", "alloy", 1.0)
PLACEHOLDER = "{{ARCREEL_JIANYING_ASSETS}}/"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _segment(segment_id: str, text: str) -> dict[str, Any]:
    return {
        "segment_id": segment_id,
        "duration_seconds": 4,
        "novel_text": text,
        "video_prompt": {"action": "Clouds move", "camera_motion": "Static"},
        "generated_assets": {
            "storyboard_image": f"storyboards/scene_{segment_id}.png",
            "video_clip": f"videos/scene_{segment_id}.mp4",
            "narration_audio": f"audio/segment_{segment_id}.wav",
        },
    }


def _install_video(project_path: Path, item: dict[str, Any], seconds: float) -> None:
    segment_id = item["segment_id"]
    storyboard = project_path / "storyboards" / f"scene_{segment_id}.png"
    storyboard.parent.mkdir(parents=True, exist_ok=True)
    storyboard.write_bytes(f"storyboard-{segment_id}".encode())
    video = project_path / "videos" / f"scene_{segment_id}.mp4"
    make_test_video(video, duration_sec=seconds, fps=10)
    preparation = admit_script_unit("segments", item).preparation
    visual = build_storyboard_video_artifact_visual_basis(
        resource_id=segment_id,
        visual_prompt=item["video_prompt"],
        storyboard_image=storyboard,
        end_frame_image=None,
        aspect_ratio="9:16",
    )
    speech = build_video_speech_basis(preparation)
    duration = build_video_duration_basis(4)
    currency = VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=4,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(4, 8),
        reference_image_limit=None,
        parent_version=0,
    )
    VersionManager(project_path).add_version(
        "videos",
        segment_id,
        "video",
        source_file=video,
        execution_checkpoint_schema_version=3,
        execution_script_file="episode_1.json",
        execution_duration_seconds=4,
        execution_request_digest="d" * 64,
        execution_provider_media=[],
        execution_generate_audio=True,
        artifact_video_currency=currency.to_dict(),
    )
    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
        ArtifactKey.episode_video(1, segment_id),
        artifact_path=f"videos/scene_{segment_id}.mp4",
        basis=currency.video_basis,
    )


def _install_narration(project_path: Path, item: dict[str, Any], seconds: float) -> None:
    segment_id = item["segment_id"]
    audio = project_path / "audio" / f"segment_{segment_id}.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(wav_bytes(seconds))
    preparation = admit_script_unit("segments", item).preparation
    audio_basis = build_narration_audio_basis(preparation, SETTINGS)
    VersionManager(project_path).add_version(
        "audio",
        segment_id,
        canonical_narration_text(preparation),
        source_file=audio,
        execution_script_file="episode_1.json",
        artifact_episode=1,
        artifact_audio_basis=ArtifactBasisDescriptor.from_basis(audio_basis).to_dict(),
        tts_basis_digest=audio_basis.digest,
        tts_actual_duration_seconds=seconds,
        tts_provider_id=SETTINGS.provider_id,
        tts_model_id=SETTINGS.model_id,
        tts_voice=SETTINGS.voice,
        tts_speed=SETTINGS.speed,
    )
    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
        ArtifactKey.episode_audio(1, segment_id),
        artifact_path=f"audio/segment_{segment_id}.wav",
        basis=audio_basis,
    )


def _setup_project(tmp_path: Path, *, narration_delivery: str = "use_tts") -> tuple[ProjectManager, Path]:
    """两个画外音分镜：S01 视频 2 秒、带 1.2 秒旁白配音，S02 视频 1.5 秒、没有旁白配音。"""
    project_path = tmp_path / "projects" / "demo"
    first, second = _segment("E1S01", "旁白一句"), _segment("E1S02", "第二段")
    _write_json(
        project_path / "project.json",
        {
            "title": "Demo",
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": False,
            "aspect_ratio": "9:16",
            "default_duration": 4,
            "characters": {},
            "narration_delivery": narration_delivery,
            "audio_backend": "openai/tts-1",
            "narration_voice": "alloy",
            "narration_speed": 1.0,
            "episodes": [{"episode": 1, "title": "One", "script_file": "scripts/episode_1.json"}],
        },
    )
    _write_json(
        project_path / "scripts" / "episode_1.json",
        {"episode": 1, "content_mode": "narration", "segments": [first, second]},
    )
    _install_video(project_path, first, 2.0)
    _install_video(project_path, second, 1.5)
    _install_narration(project_path, first, 1.2)
    return ProjectManager(tmp_path), project_path


def _append_revision(pm: ProjectManager, timeline_id: str, content: EditTimelineContent) -> None:
    store = EditTimelineStore(pm, "demo")
    document = store.find(timeline_id)
    with store.locked_episode(document.episode):
        latest = document.latest
        revision = TimelineRevision(
            number=latest.number + 1,
            parent=latest.number,
            author=CREATOR,
            summary="调整",
            created_at=datetime.now(UTC).isoformat(),
            content=content,
        )
        store.write(document.model_copy(update={"revisions": (*document.revisions, revision)}))


async def _edited_timeline(pm: ProjectManager) -> str:
    """c1 截取 0.5–1.5 秒（依据版本 1）、原声 0.5、定格 0.5 秒；c2 整段使用、原声取画外音默认值。"""
    created = await EditTimelineService(pm).create_from_script("demo", episode=1, name="完整版", author=CREATOR)
    content = EditTimelineStore(pm, "demo").find(created.timeline.id).latest.content
    first, second = content.clips
    edited = first.model_copy(
        update={
            "trim": ClipTrim(in_us=500_000, out_us=1_500_000, basis_version=1),
            "source_volume": 0.5,
            "hold_us": 500_000,
        }
    )
    _append_revision(pm, created.timeline.id, EditTimelineContent(clips=(edited, second)))
    return created.timeline.id


def _draft_content(archive_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(archive_path) as archive:
        return json.loads(archive.read("draft/draft_content.json"))


def _track(content: dict[str, Any], kind: str, name: str | None = None) -> dict[str, Any]:
    return next(
        track for track in content["tracks"] if track["type"] == kind and (name is None or track.get("name") == name)
    )


def _materials(content: dict[str, Any], group: str) -> dict[str, dict[str, Any]]:
    return {material["id"]: material for material in content["materials"][group]}


def _timing(segment: dict[str, Any]) -> tuple[int, int]:
    return segment["target_timerange"]["start"], segment["target_timerange"]["duration"]


def _subtitles(content: dict[str, Any]) -> list[tuple[str, int, int, str]]:
    texts = _materials(content, "texts")
    rows = []
    for segment in _track(content, "text")["segments"]:
        material = json.loads(texts[segment["material_id"]]["content"])
        font = material["styles"][0]["font"]
        rows.append((material["text"], *_timing(segment), font["id"]))
    return rows


async def test_with_narration_draft_maps_trim_volume_hold_narration_and_subtitles(tmp_path: Path) -> None:
    pm, project_path = _setup_project(tmp_path)
    timeline_id = await _edited_timeline(pm)

    result = await TimelineJianyingDraftService(pm).render("demo", timeline_id, narration="with_narration")

    assert result.artifact_path == f"renders/episode_1/{timeline_id}/jianying_draft.with_narration.zip"
    assert (result.revision, result.duration) == (2, 3.0)
    content = _draft_content(project_path / result.artifact_path)
    assert (content["canvas_config"]["width"], content["canvas_config"]["height"]) == (1080, 1920)
    videos = _materials(content, "videos")
    main = _track(content, "video")["segments"]
    assert [
        (
            _timing(segment),
            (segment["source_timerange"]["start"], segment["source_timerange"]["duration"]),
            segment["volume"],
            videos[segment["material_id"]]["type"],
        )
        for segment in main
    ] == [
        ((0, 1_000_000), (500_000, 1_000_000), 0.5, "video"),
        ((1_000_000, 500_000), (0, 500_000), 1.0, "photo"),
        ((1_000_000 + 500_000, 1_500_000), (0, 1_500_000), 0.3, "video"),
    ]
    hold_material = videos[main[1]["material_id"]]
    assert hold_material["path"] == f"{PLACEHOLDER}hold_c1.png"

    narration = _track(content, "audio", "旁白")["segments"]
    audios = _materials(content, "audios")
    assert [_timing(segment) for segment in narration] == [(0, 1_200_000)]
    assert audios[narration[0]["material_id"]]["path"].startswith(PLACEHOLDER)
    # S01 的字幕跟随旁白；S02 没有旁白配音，按源素材时间保留
    assert _subtitles(content) == [
        ("旁白一句", 0, 1_200_000, "7265596643066516029"),
        ("第二段", 1_500_000, 1_500_000, "7265596643066516029"),
    ]
    assert _track(content, "text")["name"] == "字幕"
    assert all(material["path"].startswith(PLACEHOLDER) for material in [*videos.values(), *audios.values()])


async def test_without_narration_draft_has_no_narration_track_and_keeps_source_time_subtitles(
    tmp_path: Path,
) -> None:
    pm, project_path = _setup_project(tmp_path)
    timeline_id = await _edited_timeline(pm)

    result = await TimelineJianyingDraftService(pm).render("demo", timeline_id, narration="without_narration")

    content = _draft_content(project_path / result.artifact_path)
    assert [track["type"] for track in content["tracks"]] == ["video", "text"]
    # S01 字幕按源素材 0–2 秒分布，只显示截取窗口 0.5–1.5 秒之内的部分
    assert [row[:3] for row in _subtitles(content)] == [("旁白一句", 0, 1_000_000), ("第二段", 1_500_000, 1_500_000)]


async def test_download_substitutes_local_draft_directory_and_jianying_version(tmp_path: Path) -> None:
    pm, project_path = _setup_project(tmp_path)
    timeline_id = await _edited_timeline(pm)
    service = TimelineJianyingDraftService(pm)
    exported = await service.render("demo", timeline_id, narration="with_narration")
    stored = project_path / exported.artifact_path
    original = stored.read_bytes()

    for draft_root, jianying_version, assets_prefix, content_name in (
        ("/Users/me/Movies/JianyingPro Drafts", "6", "/Users/me/Movies/JianyingPro Drafts", "draft_info.json"),
        ("C:\\Users\\me\\JianyingPro Drafts\\", "5", "C:\\Users\\me\\JianyingPro Drafts", "draft_content.json"),
    ):
        package, name = await service.package_download(
            "demo", timeline_id, narration="with_narration", draft_root=draft_root, jianying_version=jianying_version
        )
        try:
            assert name == "Demo_第1集_完整版_带旁白"
            assets_dir = f"{assets_prefix}/{name}/assets/"
            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
                content = json.loads(archive.read(f"{name}/{content_name}"))
            paths = [material["path"] for group in ("videos", "audios") for material in content["materials"][group]]
            assert len(paths) == 4
            assert all(path.startswith(assets_dir) for path in paths)
            assert {f"{name}/assets/{path.removeprefix(assets_dir)}" for path in paths} <= names
            assert f"{name}/draft_meta_info.json" in names
            assert {f"{name}/draft_info.json", f"{name}/draft_content.json"} & names == {f"{name}/{content_name}"}
            assert stored.read_bytes() == original
            status = await service.status("demo", timeline_id, narration="with_narration")
            assert (status.status, status.version) == (ArtifactStatus.CURRENT, 1)
        finally:
            shutil.rmtree(package.parent)


async def _status(service: TimelineJianyingDraftService, timeline_id: str, narration: Any) -> tuple[str, int | None]:
    status = await service.status("demo", timeline_id, narration=narration)
    return status.status.value, status.version


async def test_every_new_revision_makes_the_draft_stale_and_each_export_bumps_its_version(tmp_path: Path) -> None:
    pm, _project_path = _setup_project(tmp_path)
    timeline_id = await _edited_timeline(pm)
    service = TimelineJianyingDraftService(pm)
    timelines = EditTimelineService(pm)

    assert await _status(service, timeline_id, "with_narration") == ("missing", None)
    first = await service.render("demo", timeline_id, narration="with_narration")
    assert (first.version, await _status(service, timeline_id, "with_narration")) == (1, ("current", 1))
    assert await _status(service, timeline_id, "without_narration") == ("missing", None)

    # 只改剪辑理由也产生新修订，旧修订导出的草稿随之过期
    await timelines.edit(
        "demo",
        timeline_id,
        base_revision=2,
        summary="补充理由",
        operations=[SetReason(op="set_reason", clip="c2", reason="保留完整动作")],
        author=CREATOR,
    )
    assert await _status(service, timeline_id, "with_narration") == ("stale", 1)

    # 改了音量再改回原值，内容与修订 3 相同，修订 3 导出的草稿仍然过期
    await service.render("demo", timeline_id, narration="with_narration")
    for revision, volume in ((3, 0.1), (4, 0.3)):
        await timelines.edit(
            "demo",
            timeline_id,
            base_revision=revision,
            summary="调整音量",
            operations=[SetVolume(op="set_volume", clip="c2", volume=volume)],
            author=CREATOR,
        )
    assert await _status(service, timeline_id, "with_narration") == ("stale", 2)

    old = await service.render("demo", timeline_id, narration="with_narration", revision=2)
    assert (old.revision, await _status(service, timeline_id, "with_narration")) == (2, ("stale", 3))
    latest = await service.render("demo", timeline_id, narration="with_narration")
    assert (latest.revision, await _status(service, timeline_id, "with_narration")) == (5, ("current", 4))


async def test_draft_stays_stale_and_claimed_while_a_new_video_has_no_presentation_yet(tmp_path: Path) -> None:
    pm, project_path = _setup_project(tmp_path)
    timeline_id = await _edited_timeline(pm)
    service = TimelineJianyingDraftService(pm)
    await service.render("demo", timeline_id, narration="with_narration")

    _install_video(project_path, _segment("E1S02", "第二段"), 1.0)
    key = ArtifactKey.episode_jianying_draft(1, timeline_id, "with_narration")

    assert reconcile_artifact_target_claims(project_path, [key]) is False
    assert key in ProjectArtifactManifestAdapter(project_path).snapshot_entries()
    assert (await service.status("demo", timeline_id, narration="with_narration")).status is ArtifactStatus.STALE


async def test_export_is_refused_on_blocking_issues_and_unavailable_narration_variant(tmp_path: Path) -> None:
    pm, project_path = _setup_project(tmp_path, narration_delivery="post_production")
    timeline_id = await _edited_timeline(pm)
    service = TimelineJianyingDraftService(pm)

    with pytest.raises(JianyingDraftError) as variant_refused:
        await service.check("demo", timeline_id, narration="with_narration")
    assert variant_refused.value.code == "jianying_draft_narration_unavailable"

    script_path = project_path / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["segments"].append(_segment("E1S03", "还没有视频"))
    _write_json(script_path, script)
    blocked_id = (
        await EditTimelineService(pm).create_from_script("demo", episode=1, name="新版", author=CREATOR)
    ).timeline.id

    with pytest.raises(JianyingDraftError) as blocked:
        await service.render("demo", blocked_id, narration="without_narration")
    assert blocked.value.code == "jianying_draft_blocked"
    assert [(issue["code"], issue["unit_id"]) for issue in blocked.value.params["issues"]] == [
        ("video_missing", "E1S03")
    ]
    assert not (project_path / "renders" / "episode_1" / blocked_id).exists()
