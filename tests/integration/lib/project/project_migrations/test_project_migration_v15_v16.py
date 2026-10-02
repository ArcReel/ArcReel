from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import pytest

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactKind,
    ArtifactManifestEntry,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_planner import TargetStatePlanner
from lib.artifacts.artifact_version_provenance import VIDEO_CURRENCY_DURATION_FIELD, parse_typed_media_version_target
from lib.artifacts.media_artifact_currency import build_current_video_artifact_basis
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.episode.episode_ids import EPISODE_ID_HIGH_WATER_KEY, allocate_episode_ids
from lib.episode.episode_layout import build_episode_layout
from lib.episode.episode_reset import EpisodeResetError, reset_episode_planning
from lib.episode.episode_sources import discover_sources, planning_start, source_snapshot_path
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_failure import ProjectMigrationError
from lib.project.project_migration_report import load_migration_report
from lib.project.project_migrations import CURRENT_SCHEMA_VERSION
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_migrations.v15_to_v16_edit_decisions import migrate_v15_to_v16
from lib.project.resource_paths import resource_relative_path
from lib.speech.narration_config import TtsSynthesisSettings
from lib.speech.speech_presentation import presentation_artifact_paths
from lib.workflow.workflow_state import WorkflowStateService
from server.services.presentation.presentation_read_model import PresentationReadModelService
from tests.legacy_project_shapes import (
    LEGACY_CHAPTER_TEN,
    add_legacy_manual_uploads,
    advance_project_schema,
    legacy_transition_presentation_basis,
    write_legacy_ad_reference_video_project,
    write_legacy_episode_id_remnants_project,
    write_legacy_episode_sources_project,
    write_legacy_presentation_project,
    write_legacy_reference_video_project,
    write_legacy_script_plan_project,
    write_legacy_storyboard_project,
    write_legacy_tts_narration_project,
)

_SUBTITLE_PATH, _PRESENTATION_PATH = presentation_artifact_paths(1, "E1S01", "post_production")
_SUBTITLE_KEY = ArtifactKey.episode_subtitle(1, "E1S01", "post_production")
_PRESENTATION_KEY = ArtifactKey.episode_presentation(1, "E1S01", "post_production")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _without_transitions(script: dict[str, Any]) -> dict[str, Any]:
    return {
        key: [{k: v for k, v in item.items() if k != "transition_to_next"} for item in value]
        if key in {"segments", "scenes", "shots", "video_units"}
        else value
        for key, value in script.items()
    }


def _status(project_dir: Path, key: ArtifactKey, artifact_path: str) -> ArtifactStatus:
    return ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=artifact_path).status


def _v15_legacy_presentation(tmp_path: Path) -> Path:
    project_dir = write_legacy_presentation_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=15)
    return project_dir


def _legacy_digest_of(presentation: dict[str, Any], *, transition: str) -> str:
    return legacy_transition_presentation_basis(
        variant=presentation["variant"],
        transition=transition,
        video={key: presentation["video"][key] for key in ("basis", "content_digest", "actual_duration_seconds")},
        subtitle=presentation["subtitle_basis"],
        narration_audio=None,
        provider_audio_enabled=presentation["video"]["audio_enabled"],
    ).digest


def test_upgrade_drops_every_script_transition_and_keeps_the_rest(tmp_path: Path) -> None:
    storyboard = write_legacy_storyboard_project(tmp_path)
    reference = write_legacy_reference_video_project(tmp_path)
    before: dict[Path, dict[str, Any]] = {}
    for project_dir in (storyboard, reference):
        advance_project_schema(project_dir, to_version=15)
        script_path = project_dir / "scripts" / "episode_1.json"
        before[project_dir] = _read_json(script_path)
        assert {item["transition_to_next"] for item in next(iter(_items(before[project_dir])))} > {"cut"}

    for project_dir in (storyboard, reference):
        assert migrate_project_dir(project_dir) is True

        script_path = project_dir / "scripts" / "episode_1.json"
        assert _read_json(script_path) == _without_transitions(before[project_dir])
        assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_SCHEMA_VERSION
        assert list(script_path.parent.glob("episode_1.json.bak.v15-*"))


def _items(script: dict[str, Any]) -> list[list[dict[str, Any]]]:
    return [script[key] for key in ("segments", "scenes", "shots", "video_units") if key in script]


def test_the_whole_chain_from_an_old_install_leaves_no_transition(tmp_path: Path) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")

    migrate_project_dir(project_dir)

    segments = _read_json(project_dir / "scripts" / "episode_1.json")["segments"]
    assert segments
    assert all("transition_to_next" not in segment for segment in segments)
    assert _read_json(project_dir / "project.json")["narration_delivery"] == "post_production"
    summary = WorkflowStateService(ProjectManager(tmp_path)).get_project_summary(project_dir.name)
    assert (summary.episodes[0].videos.available, summary.episodes[0].videos.stale) == (2, 0)


def test_the_whole_chain_drops_the_retired_asset_inventory_marker(tmp_path: Path) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    assert "asset_inventory" in _read_json(project_dir / "project.json")["workflow"]

    migrate_project_dir(project_dir)

    assert "workflow" not in _read_json(project_dir / "project.json")


@pytest.mark.parametrize("converted", [False, True], ids=["indexed-shots", "interrupted-conversion"])
def test_schema6_ad_reference_upgrade_preserves_units_and_paid_videos(tmp_path: Path, converted: bool) -> None:
    project_dir = write_legacy_ad_reference_video_project(tmp_path / "projects", converted=converted)
    videos = {path.name: path.read_bytes() for path in (project_dir / "reference_videos").glob("*.mp4")}

    assert migrate_project_dir(project_dir) is True

    script = _read_json(project_dir / "scripts" / "episode_1.json")
    assert script["video_units"] == [
        {
            "unit_id": f"E1U{index:02d}",
            "text": f"镜头{index}；缓慢转动",
            "duration_seconds": 8,
            "note": None,
            "generated_assets": {"video_clip": f"reference_videos/E1U{index:02d}.mp4", "status": "completed"},
        }
        for index in (1, 2)
    ]
    assert "shots" not in script
    assert "reference_units" not in script
    assert script["duration_seconds"] == 16
    assert {path.name: path.read_bytes() for path in (project_dir / "reference_videos").glob("*.mp4")} == videos
    summary = WorkflowStateService(ProjectManager(tmp_path)).get_project_summary(project_dir.name)
    episode = summary.episodes[0]
    assert episode.script_status == "generated"
    assert episode.item_count == 2
    assert (episode.videos.total, episode.videos.available, episode.videos.stale) == (2, 2, 0)


def test_schema8_split_reference_upgrade_preserves_text_duration_and_paid_assets(tmp_path: Path) -> None:
    project_dir = write_legacy_reference_video_project(tmp_path / "projects", schema_version=8, split_unit_text=True)
    before = _read_json(project_dir / "scripts" / "episode_1.json")
    before["video_units"][1]["migration_requires_content_replan"] = True
    _write_json(project_dir / "scripts" / "episode_1.json", before)

    assert migrate_project_dir(project_dir) is True

    migrated = _read_json(project_dir / "scripts" / "episode_1.json")
    expected = []
    for index, unit in enumerate(before["video_units"], start=1):
        normalized = {
            key: value
            for key, value in unit.items()
            if key not in {"shots", "references", "transition_to_next", "migration_requires_content_replan"}
        }
        normalized.update(text=f"第{index}个单元的画面描述。", source_text="")
        if index == 2:
            normalized["needs_replan"] = True
        expected.append(normalized)
    assert migrated["video_units"] == expected
    summary = WorkflowStateService(ProjectManager(tmp_path)).get_project_summary(project_dir.name)
    assert summary.episodes[0].item_count == 2
    assert (summary.episodes[0].videos.total, summary.episodes[0].videos.available) == (2, 2)
    status = WorkflowStateService(ProjectManager(tmp_path)).get_status(project_dir.name, 1)
    assert status.artifacts["videos"]["current_ids"] == ["E1U01"]
    assert status.artifacts["videos"]["stale_ids"] == ["E1U02"]


def test_report_describes_the_completed_chain_and_keeps_legacy_audio_skips(tmp_path: Path) -> None:
    project_dir = write_legacy_reference_video_project(tmp_path / "projects", with_legacy_audio=True)
    advance_project_schema(project_dir, to_version=15)

    assert migrate_project_dir(project_dir) is True

    report = load_migration_report(project_dir)
    assert report is not None
    assert (report.from_schema_version, report.to_schema_version) == (15, CURRENT_SCHEMA_VERSION)
    assert report.registered["episode-video"] == 2
    assert report.registered["episode-script"] == 1
    assert {(item.kind, item.resource_id) for item in report.skipped} == {
        ("episode-audio", "E1U01"),
        ("episode-audio", "E1U02"),
    }
    status = WorkflowStateService(ProjectManager(tmp_path)).get_status(project_dir.name, 1)
    assert status.migration_report == report
    # 旧音频的选中版本记录没有 TTS 设置，不被登记，项目判为后期配音
    assert _read_json(project_dir / "project.json")["narration_delivery"] == "post_production"


async def _tts_status(project_dir: Path, unit_id: str) -> ArtifactStatus:
    """工作流状态与缺失补齐读到的旁白配音时效。"""

    comparison = ArtifactCurrencyResolver(project_dir).compare(
        ArtifactKey.episode_audio(1, unit_id), artifact_path=resource_relative_path("audio", unit_id)
    )
    return comparison.status


async def test_project_with_registered_tts_audio_becomes_tts_and_its_audio_stays_current(
    tmp_path: Path, db_factory
) -> None:
    used = TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Ethan", 1.2)
    project_dir = write_legacy_tts_narration_project(tmp_path / "projects", settings=(used, used))

    assert migrate_project_dir(project_dir) is True

    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == CURRENT_SCHEMA_VERSION
    assert {key: project.get(key) for key in ("narration_delivery", "audio_backend", "narration_voice")} == {
        "narration_delivery": "use_tts",
        "audio_backend": "dashscope/qwen3-tts-flash",
        "narration_voice": "Ethan",
    }
    assert project["narration_speed"] == 1.2
    assert await _tts_status(project_dir, "E1S1") is ArtifactStatus.CURRENT
    assert await _tts_status(project_dir, "E1S2") is ArtifactStatus.CURRENT

    async with db_factory() as session:
        service = ConfigService(session)
        await service.set_setting("default_audio_backend", "dashscope/qwen-tts-latest")
        await service.set_setting("narration_voice", "Cherry")
        await service.set_setting("narration_speed", "0.8")
        await session.commit()
    defaults, voice, speed = await ConfigResolver(db_factory).default_narration_tts()
    assert (defaults.model_id, voice, speed) == ("qwen-tts-latest", "Cherry", 0.8)
    assert _read_json(project_dir / "project.json") == project
    assert await _tts_status(project_dir, "E1S1") is ArtifactStatus.CURRENT

    manager = ProjectManager(tmp_path)
    entries = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    for delivery in ("post_production", "use_tts"):
        manager.update_project(
            project_dir.name, lambda value, delivery=delivery: value.update(narration_delivery=delivery)
        )
        saved = _read_json(project_dir / "project.json")
        assert {key: saved.get(key) for key in ("audio_backend", "narration_voice", "narration_speed")} == {
            "audio_backend": "dashscope/qwen3-tts-flash",
            "narration_voice": "Ethan",
            "narration_speed": 1.2,
        }
        assert saved["narration_delivery"] == delivery
        assert ProjectArtifactManifestAdapter(project_dir).snapshot_entries() == entries
        assert await _tts_status(project_dir, "E1S1") is ArtifactStatus.CURRENT
        assert await _tts_status(project_dir, "E1S2") is ArtifactStatus.CURRENT
        summary = WorkflowStateService(manager).get_project_summary(project_dir.name)
        assert (summary.episodes[0].videos.available, summary.episodes[0].videos.stale) == (2, 0)

    manager.update_project(project_dir.name, lambda value: value.update(narration_voice="Cherry"))
    assert await _tts_status(project_dir, "E1S1") is ArtifactStatus.STALE
    status = WorkflowStateService(manager).get_status(project_dir.name, 1)
    assert status.artifacts["audio"]["stale_ids"] == ["E1S1", "E1S2"]
    manager.update_project(project_dir.name, lambda value: value.update(narration_voice="Ethan"))
    assert await _tts_status(project_dir, "E1S1") is ArtifactStatus.CURRENT


async def test_tts_snapshot_takes_the_most_recently_generated_audio_settings(tmp_path: Path) -> None:
    project_dir = write_legacy_tts_narration_project(
        tmp_path / "projects",
        settings=(
            TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Ethan", 1.2),
            TtsSynthesisSettings("openai", "tts-1", "alloy", None),
        ),
    )
    advance_project_schema(project_dir, to_version=15)

    migrate_v15_to_v16(project_dir)
    advance_project_schema(project_dir, to_version=CURRENT_SCHEMA_VERSION)

    project = _read_json(project_dir / "project.json")
    assert project["narration_delivery"] == "use_tts"
    assert (project["audio_backend"], project["narration_voice"]) == ("openai/tts-1", "alloy")
    # 快照不设语速：旧项目里「跟随全局默认」的语速字段不再保留
    assert "narration_speed" not in project
    assert await _tts_status(project_dir, "E1S2") is ArtifactStatus.CURRENT
    assert await _tts_status(project_dir, "E1S1") is ArtifactStatus.STALE


@pytest.mark.parametrize("schema_version", [13, 15])
@pytest.mark.parametrize("uses_project_default", [False, True], ids=["unit-duration", "project-default"])
async def test_upgrade_preserves_paid_videos_raised_by_tts_but_does_not_refresh_stale_videos(
    tmp_path: Path, schema_version: int, uses_project_default: bool
) -> None:
    settings = TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None)
    project_dir = write_legacy_tts_narration_project(
        tmp_path / "projects", settings=(settings, settings), raised_video_duration_seconds=8
    )
    if uses_project_default:
        project_path = project_dir / "project.json"
        project = _read_json(project_path)
        project["default_duration"] = 4
        _write_json(project_path, project)
        script_path = project_dir / "scripts" / "episode_1.json"
        script = _read_json(script_path)
        for item in script["segments"]:
            del item["duration_seconds"]
        _write_json(script_path, script)
    advance_project_schema(project_dir, to_version=schema_version)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    legacy_target = TargetStatePlanner(project_dir).plan()
    for unit_id in ("E1S1", "E1S2"):
        key = ArtifactKey.episode_video(1, unit_id)
        assert adapter.get_entry(key) == legacy_target.entries[key]
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["segments"][1]["video_prompt"]["action"] = "已经改写的画面"
    _write_json(script_path, script)
    stale_entry = adapter.get_entry(ArtifactKey.episode_video(1, "E1S2"))
    versions_path = project_dir / "versions" / "versions.json"
    before = _read_json(versions_path)
    media = {path.name: path.read_bytes() for path in (project_dir / "videos").glob("*.mp4")}

    assert migrate_project_dir(project_dir) is True

    status = WorkflowStateService(ProjectManager(tmp_path)).get_status(project_dir.name, 1)
    assert status.artifacts["videos"]["current_ids"] == ["E1S1"]
    assert status.artifacts["videos"]["stale_ids"] == ["E1S2"]
    assert adapter.get_entry(ArtifactKey.episode_video(1, "E1S2")) == stale_entry
    after = _read_json(versions_path)
    selected = after["videos"]["E1S1"]["versions"][0]
    assert selected == {**before["videos"]["E1S1"]["versions"][0], VIDEO_CURRENCY_DURATION_FIELD: 4}
    assert after["videos"]["E1S2"] == before["videos"]["E1S2"]
    assert selected["execution_duration_seconds"] == 8
    assert selected["artifact_video_currency"]["request_duration_seconds"] == 8
    assert adapter.get_entry(ArtifactKey.episode_video(1, "E1S1")).basis_digest == (
        parse_typed_media_version_target("videos", selected).basis.digest
    )
    assert {path.name: path.read_bytes() for path in (project_dir / "videos").glob("*.mp4")} == media

    async def probe(_path: Path) -> float:
        return 8.0

    preview = await PresentationReadModelService(ProjectManager(tmp_path), duration_probe=probe).materialize_unit(
        project_name=project_dir.name, resource_type="videos", resource_id="E1S1", variant="post_production"
    )
    assert preview.presentation.currency == "current"
    report = load_migration_report(project_dir)
    assert report is not None
    assert report.registered["episode-video"] == 2
    assert list(versions_path.parent.glob("versions.json.bak.v15-*"))


def test_project_without_registered_narration_audio_becomes_post_production(tmp_path: Path) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=15)
    legacy = _read_json(project_dir / "project.json")
    legacy.update({"audio_backend": "dashscope", "narration_voice": "Cherry"})
    _write_json(project_dir / "project.json", legacy)

    migrate_v15_to_v16(project_dir)

    project = _read_json(project_dir / "project.json")
    assert legacy["source_kind"] == "novel"
    assert project == {
        **{key: value for key, value in legacy.items() if key not in {"source_kind", "workflow"}},
        "episodes": [{**entry, "source_origin": "none"} for entry in legacy["episodes"]],
        "whole_source_files": [{"source_file": "source/1-7-0227.txt"}],
        "narration_delivery": "post_production",
        EPISODE_ID_HIGH_WATER_KEY: 1,
        # 整本源文还没有切出集
        "source_remaining": True,
        "schema_version": 16,
    }


@pytest.mark.parametrize("invalid_audio", ["missing-claim", "wrong-script"])
def test_upgrade_does_not_refresh_a_raised_video_whose_narration_is_not_current(
    tmp_path: Path, invalid_audio: str
) -> None:
    project_dir = write_legacy_tts_narration_project(
        tmp_path / "projects",
        settings=(TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None),),
        raised_video_duration_seconds=8,
    )
    advance_project_schema(project_dir, to_version=15)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    key = ArtifactKey.episode_video(1, "E1S1")
    entry = adapter.get_entry(key)
    versions_path = project_dir / "versions" / "versions.json"
    versions = _read_json(versions_path)
    if invalid_audio == "missing-claim":
        adapter.delete_entry(ArtifactKey.episode_audio(1, "E1S1"))
    else:
        versions["audio"]["E1S1"]["versions"][0]["execution_script_file"] = "episode_2.json"
        _write_json(versions_path, versions)
    selected = versions["videos"]["E1S1"]["versions"][0]
    before = build_current_video_artifact_basis(
        project_path=project_dir,
        project=_read_json(project_dir / "project.json"),
        script=_read_json(project_dir / "scripts" / "episode_1.json"),
        resource_type="videos",
        resource_id="E1S1",
        version_metadata=selected,
    )
    assert before is not None
    assert before.digest != entry.basis_digest

    migrate_project_dir(project_dir)

    assert adapter.get_entry(key) == entry
    assert _read_json(versions_path)["videos"] == versions["videos"]
    status = WorkflowStateService(ProjectManager(tmp_path)).get_status(project_dir.name, 1)
    assert status.artifacts["videos"]["stale_ids"] == ["E1S1"]
    assert status.artifacts["videos"]["current_ids"] == []


@pytest.mark.parametrize("manifest_committed", [False, True], ids=["versions-only", "versions-and-manifest"])
def test_raised_video_duration_rebase_can_resume_before_project_version_commit(
    tmp_path: Path, manifest_committed: bool
) -> None:
    project_dir = write_legacy_tts_narration_project(
        tmp_path / "projects",
        settings=(TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None),),
        raised_video_duration_seconds=8,
    )
    advance_project_schema(project_dir, to_version=15)
    project_path = project_dir / "project.json"
    project_bytes = project_path.read_bytes()
    adapter = ProjectArtifactManifestAdapter(project_dir)
    old_entries = adapter.snapshot_entries()
    migrate_project_dir(project_dir)
    new_entries = adapter.snapshot_entries()
    versions_path = project_dir / "versions" / "versions.json"
    new_versions = versions_path.read_bytes()

    project_path.write_bytes(project_bytes)
    if not manifest_committed:
        adapter.replace_entries_atomically(old_entries)
    assert migrate_project_dir(project_dir) is True

    assert adapter.snapshot_entries() == new_entries
    assert versions_path.read_bytes() == new_versions
    status = WorkflowStateService(ProjectManager(tmp_path)).get_status(project_dir.name, 1)
    assert status.artifacts["videos"]["current_ids"] == ["E1S1"]


async def test_current_presentation_and_subtitle_stay_current_and_preview_rebuilds_the_same_files(
    tmp_path: Path,
) -> None:
    project_dir = _v15_legacy_presentation(tmp_path)
    legacy = _read_json(project_dir / _PRESENTATION_PATH)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    # v15 的读法：登记等于按实时转场算出的旧依据，即为时新。
    assert adapter.get_entry(_PRESENTATION_KEY) == ArtifactManifestEntry(
        _PRESENTATION_PATH, _legacy_digest_of(legacy, transition="fade")
    )
    subtitle_entry = adapter.get_entry(_SUBTITLE_KEY)
    subtitle_bytes = (project_dir / _SUBTITLE_PATH).read_bytes()

    migrate_project_dir(project_dir)

    migrated = _read_json(project_dir / _PRESENTATION_PATH)
    assert "transition_to_next" not in migrated
    assert migrated["presentation_basis"]["kind_version"] == 3
    assert {k: v for k, v in migrated.items() if k != "presentation_basis"} == {
        k: v for k, v in legacy.items() if k not in {"presentation_basis", "transition_to_next"}
    }
    assert (project_dir / _SUBTITLE_PATH).read_bytes() == subtitle_bytes
    assert adapter.get_entry(_SUBTITLE_KEY) == subtitle_entry
    assert _status(project_dir, _SUBTITLE_KEY, _SUBTITLE_PATH) is ArtifactStatus.CURRENT
    assert _status(project_dir, _PRESENTATION_KEY, _PRESENTATION_PATH) is ArtifactStatus.CURRENT
    migrated_entries = adapter.snapshot_entries()

    async def probe(_path: Path) -> float | None:
        return 4.0

    result = await PresentationReadModelService(ProjectManager(tmp_path), duration_probe=probe).materialize_unit(
        project_name=project_dir.name,
        resource_type="videos",
        resource_id="E1S01",
        variant="post_production",
    )

    assert result.presentation_artifact_path == _PRESENTATION_PATH
    assert result.presentation.currency == "current"
    assert _read_json(project_dir / _PRESENTATION_PATH) == migrated
    assert adapter.snapshot_entries() == migrated_entries


def test_presentation_already_stale_for_other_inputs_stays_stale(tmp_path: Path) -> None:
    project_dir = _v15_legacy_presentation(tmp_path)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["segments"][0]["novel_text"] = "雨停之后"
    _write_json(script_path, script)

    migrate_project_dir(project_dir)

    assert _status(project_dir, _SUBTITLE_KEY, _SUBTITLE_PATH) is ArtifactStatus.STALE
    assert _status(project_dir, _PRESENTATION_KEY, _PRESENTATION_PATH) is ArtifactStatus.STALE


def test_presentation_stale_only_by_its_transition_becomes_current(tmp_path: Path) -> None:
    project_dir = _v15_legacy_presentation(tmp_path)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["segments"][0]["transition_to_next"] = "cut"
    _write_json(script_path, script)

    migrate_project_dir(project_dir)

    assert _status(project_dir, _PRESENTATION_KEY, _PRESENTATION_PATH) is ArtifactStatus.CURRENT


def test_rerun_after_the_manifest_was_rebased_but_the_file_was_not(tmp_path: Path) -> None:
    project_dir = _v15_legacy_presentation(tmp_path)
    project_bytes = (project_dir / "project.json").read_bytes()
    legacy_bytes = (project_dir / _PRESENTATION_PATH).read_bytes()
    migrate_project_dir(project_dir)
    migrated_bytes = (project_dir / _PRESENTATION_PATH).read_bytes()
    entries = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()

    (project_dir / _PRESENTATION_PATH).write_bytes(legacy_bytes)
    (project_dir / "project.json").write_bytes(project_bytes)
    migrate_v15_to_v16(project_dir)
    advance_project_schema(project_dir, to_version=CURRENT_SCHEMA_VERSION)

    assert (project_dir / _PRESENTATION_PATH).read_bytes() == migrated_bytes
    assert ProjectArtifactManifestAdapter(project_dir).snapshot_entries() == entries
    assert _status(project_dir, _PRESENTATION_KEY, _PRESENTATION_PATH) is ArtifactStatus.CURRENT


def test_presentation_file_that_does_not_match_its_recorded_basis_is_left_alone(tmp_path: Path) -> None:
    project_dir = _v15_legacy_presentation(tmp_path)
    tampered = _read_json(project_dir / _PRESENTATION_PATH)
    tampered["transition_to_next"] = "dissolve"
    _write_json(project_dir / _PRESENTATION_PATH, tampered)
    entry = ProjectArtifactManifestAdapter(project_dir).get_entry(_PRESENTATION_KEY)

    migrate_project_dir(project_dir)

    assert _read_json(project_dir / _PRESENTATION_PATH) == tampered
    assert ProjectArtifactManifestAdapter(project_dir).get_entry(_PRESENTATION_KEY) == entry


def test_corrupt_input_is_refused_before_any_file_is_rewritten(tmp_path: Path) -> None:
    project_dir = _v15_legacy_presentation(tmp_path)
    (project_dir / "versions" / "versions.json").write_text("{", encoding="utf-8")
    before = {path: path.read_bytes() for path in project_dir.rglob("*") if path.is_file()}

    with pytest.raises(ProjectMigrationError, match="version metadata"):
        migrate_v15_to_v16(project_dir)

    assert {path: path.read_bytes() for path in project_dir.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("storyboard_shape", ["blank-prompt", "missing-sheet", "generated-basis"])
def test_upgrade_registers_uploaded_videos_and_storyboards_by_their_bytes(
    tmp_path: Path, storyboard_shape: Literal["blank-prompt", "missing-sheet", "generated-basis"]
) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=15)
    add_legacy_manual_uploads(project_dir, video_unit="E1S1", storyboard_unit="E1S2", storyboard_shape=storyboard_shape)
    video_key, video_path = ArtifactKey.episode_video(1, "E1S1"), "videos/scene_E1S1.mp4"
    storyboard_key, storyboard_path = ArtifactKey.episode_storyboard(1, "E1S2"), "storyboards/scene_E1S2.png"
    adapter = ProjectArtifactManifestAdapter(project_dir)
    old_entry = adapter.get_entry(storyboard_key)
    assert (old_entry is not None) == (storyboard_shape == "generated-basis")

    assert migrate_project_dir(project_dir) is True

    assert _status(project_dir, video_key, video_path) is ArtifactStatus.CURRENT
    assert _status(project_dir, storyboard_key, storyboard_path) is ArtifactStatus.CURRENT
    assert adapter.get_entry(storyboard_key) != old_entry
    assert list(project_dir.glob(".arcreel_artifacts.json.bak.v15-*"))
    summary = WorkflowStateService(ProjectManager(tmp_path)).get_project_summary(project_dir.name)
    assert (summary.episodes[0].storyboards.available, summary.episodes[0].videos.available) == (2, 2)

    with (project_dir / "scripts" / "episode_1.json").open("r+", encoding="utf-8") as handle:
        script = json.load(handle)
        for segment in script["segments"]:
            segment["image_prompt"] = {"scene": "改写后的画面"}
            segment["video_prompt"] = {"action": "改写后的动作", "camera_motion": "Static"}
        handle.seek(0)
        handle.truncate()
        json.dump(script, handle, ensure_ascii=False)
    assert _status(project_dir, video_key, video_path) is ArtifactStatus.CURRENT
    assert _status(project_dir, storyboard_key, storyboard_path) is ArtifactStatus.CURRENT


def test_the_whole_chain_registers_uploads_made_before_the_manifest(tmp_path: Path) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    add_legacy_manual_uploads(project_dir, video_unit="E1S1", storyboard_unit="E1S2")

    assert migrate_project_dir(project_dir) is True

    assert _status(project_dir, ArtifactKey.episode_video(1, "E1S1"), "videos/scene_E1S1.mp4") is ArtifactStatus.CURRENT
    assert (
        _status(project_dir, ArtifactKey.episode_storyboard(1, "E1S2"), "storyboards/scene_E1S2.png")
        is ArtifactStatus.CURRENT
    )


# ---------------------------------------------------------------------------
# 集 ID 与播出顺序分离
# ---------------------------------------------------------------------------


def test_high_water_covers_ledger_disk_remnants_and_recorded_ids(tmp_path: Path) -> None:
    project_dir = write_legacy_episode_id_remnants_project(tmp_path / "projects")
    looked_up: list[str] = []

    def recorded_episode_ids(project_name: str) -> int:
        looked_up.append(project_name)
        return 9

    migrate_project_dir(project_dir, recorded_episode_ids=recorded_episode_ids)

    project = _read_json(project_dir / "project.json")
    assert looked_up == [project_dir.name]
    assert project[EPISODE_ID_HIGH_WATER_KEY] == 9
    assert allocate_episode_ids(project, 1) == [10]


def test_high_water_without_recorded_ids_covers_disk_remnants(tmp_path: Path) -> None:
    project_dir = write_legacy_episode_id_remnants_project(tmp_path / "projects")

    migrate_project_dir(project_dir)

    assert _read_json(project_dir / "project.json")[EPISODE_ID_HIGH_WATER_KEY] == 8


@pytest.mark.parametrize("record_remnant", ["version_history", "grid"])
def test_high_water_covers_records_left_after_the_media_was_deleted(tmp_path: Path, record_remnant) -> None:
    project_dir = write_legacy_episode_id_remnants_project(tmp_path / "projects", record_remnant=record_remnant)
    migrate_project_dir(project_dir)
    project = ProjectManager.for_project_dir(project_dir).load_project(project_dir.name)
    assert allocate_episode_ids(project, 1) == [42]


def test_high_water_ignores_asset_names_that_look_like_item_ids(tmp_path: Path) -> None:
    project_dir = write_legacy_episode_id_remnants_project(tmp_path / "projects", asset_named_like_an_item=True)

    migrate_project_dir(project_dir)

    project = ProjectManager.for_project_dir(project_dir).load_project(project_dir.name)
    assert project[EPISODE_ID_HIGH_WATER_KEY] == 8
    assert allocate_episode_ids(project, 1) == [9]


def test_script_plans_stale_only_by_the_next_episode_outline_stay_current(tmp_path: Path) -> None:
    """下集大纲改为「没有规划数据时给标题」：只因此变了依据的脚本规划登记随升级改写，仍是时新。"""

    project_dir = write_legacy_script_plan_project(tmp_path / "projects", variant="drama", schema_version=10)
    advance_project_schema(project_dir, to_version=15)
    plan_path = "drafts/episode_1/script_plan_normalized_script.json"
    key = ArtifactKey.episode_script_plan(1)
    registered = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()[key]
    assert TargetStatePlanner(project_dir, allow_stale_formal_targets=True).plan().entries[key] == registered

    migrate_project_dir(project_dir)

    assert _status(project_dir, key, plan_path) is ArtifactStatus.CURRENT
    status = WorkflowStateService(ProjectManager(tmp_path)).get_status(project_dir.name, 1)
    assert status.artifacts["script_plan"]["state"] == "current"


def _register_script_plans_as_v15_did(project_dir: Path, variant: str) -> None:
    """按 v15 的口径重登记脚本规划：剧情演绎的依据含项目级类型，参考生视频的依据不分类型。"""
    project = _read_json(project_dir / "project.json")
    view = project
    if variant == "drama":
        kind = project["source_kind"]
        view = {
            **project,
            "episodes": [{**entry, "source_origin": "own", "source_kind": kind} for entry in project["episodes"]],
        }
    adapter = ProjectArtifactManifestAdapter(project_dir)
    stored = adapter.snapshot_entries()
    planned = TargetStatePlanner(
        project_dir, project_bytes=json.dumps(view).encode(), allow_stale_formal_targets=True
    ).plan()
    replacements = {
        key: entry
        for key, entry in planned.entries.items()
        if key.kind is ArtifactKind.EPISODE_SCRIPT_PLAN and stored.get(key) != entry
    }
    assert (variant == "drama") == bool(replacements)
    assert not replacements or adapter.replace_entries_if_matches_atomically(
        expected={key: stored[key] for key in replacements}, replacements=replacements
    )


@pytest.mark.parametrize(
    ("variant", "plan_name", "expected"),
    [
        ("drama", "script_plan_normalized_script.json", ArtifactStatus.CURRENT),
        ("reference_video", "script_plan_reference_units.json", ArtifactStatus.STALE),
    ],
)
def test_screenplay_project_records_the_source_kind_on_each_source(
    tmp_path: Path, variant: str, plan_name: str, expected: ArtifactStatus
) -> None:
    """项目级类型补记到各集原文上：剧情演绎的脚本规划依据不变；参考生视频此前按小说出稿，改后读为过期。"""

    project_dir = write_legacy_script_plan_project(
        tmp_path / "projects", variant=variant, schema_version=10, source_kind="screenplay"
    )
    advance_project_schema(project_dir, to_version=15)
    _register_script_plans_as_v15_did(project_dir, variant)

    migrate_project_dir(project_dir)

    project = _read_json(project_dir / "project.json")
    assert "source_kind" not in project
    assert [(entry["source_origin"], entry["source_kind"]) for entry in project["episodes"]] == [
        ("own", "screenplay")
    ] * 3
    key = ArtifactKey.episode_script_plan(1)
    assert _status(project_dir, key, f"drafts/episode_1/{plan_name}") is expected


def _migrated_episode_sources(tmp_path: Path, **shape: bool) -> tuple[Path, dict[str, Any]]:
    project_dir = write_legacy_episode_sources_project(tmp_path / "projects", **shape)
    migrate_project_dir(project_dir)
    return project_dir, _read_json(project_dir / "project.json")


def test_whole_source_files_keep_the_old_file_name_order_and_the_planning_start(tmp_path: Path) -> None:
    project_dir, project = _migrated_episode_sources(tmp_path, pre_split=False)

    assert project["whole_source_files"] == [
        {"source_file": "source/第10章.txt"},
        {"source_file": "source/第2章.txt"},
    ]
    assert "planning_cursor" not in project
    docs = discover_sources(project_dir, project)
    assert planning_start(project, docs) == ("source/第10章.txt", LEGACY_CHAPTER_TEN.index("第十章结尾"))
    assert source_snapshot_path(project_dir, "source/第10章.txt").read_text(encoding="utf-8") == LEGACY_CHAPTER_TEN
    assert not source_snapshot_path(project_dir, "source/第2章.txt").exists()


def test_episode_file_without_a_ledger_entry_becomes_an_own_source_episode(tmp_path: Path) -> None:
    project_dir, project = _migrated_episode_sources(tmp_path, pre_split=False)

    assert [(entry["episode"], entry["source_origin"]) for entry in project["episodes"]] == [
        (1, "whole_source"),
        (2, "whole_source"),
        (7, "own"),
    ]
    assert (project_dir / "source" / "episode_7.txt").read_text(encoding="utf-8") == "另放进来的一集。"
    assert allocate_episode_ids(project, 1) == [8]

    pm = ProjectManager(project_dir.parent.parent)
    status = WorkflowStateService(pm).get_status(project_dir.name, 7)
    assert status.content is not None
    assert status.content.episode_source == "present"
    assert status.content.source_remaining is True
    assert status.operations["prepare_script_plan"].state == "admitted"
    # 项目列表不读源文，读迁移记下的结论：与制作状态一致，已有的集全部完成也不算项目完成
    assert project["source_remaining"] is True
    assert WorkflowStateService(pm).get_project_summary(project_dir.name, currency="registered").source_remaining


def test_screenplay_whole_source_files_and_cut_episodes_read_as_screenplay(tmp_path: Path) -> None:
    """剧情演绎的项目级类型补记到整本源文的每个文件与自带原文的集上；切出集取范围起点所在文件。"""

    project_dir = write_legacy_episode_sources_project(
        tmp_path / "projects", content_mode="drama", source_kind="screenplay"
    )
    migrate_project_dir(project_dir)

    project = _read_json(project_dir / "project.json")
    assert "source_kind" not in project
    assert project["whole_source_files"] == [
        {"source_file": "source/第10章.txt", "source_kind": "screenplay"},
        {"source_file": "source/第2章.txt", "source_kind": "screenplay"},
    ]
    assert [(entry["episode"], entry.get("source_kind")) for entry in project["episodes"]] == [
        (1, None),
        (2, None),
        (7, "screenplay"),
    ]
    layout = build_episode_layout(project_dir, project)
    assert [(file.source_file, file.source_kind) for file in layout.files] == [
        ("source/第10章.txt", "screenplay"),
        ("source/第2章.txt", "screenplay"),
    ]
    assert [(episode.episode, episode.source_kind) for episode in layout.episodes] == [
        (1, "screenplay"),
        (2, "screenplay"),
        (7, "screenplay"),
    ]


def test_pre_split_episode_files_become_own_source_episodes(tmp_path: Path) -> None:
    project_dir, project = _migrated_episode_sources(tmp_path, pre_split=True)

    assert project["whole_source_files"] == []
    assert [(entry["episode"], entry["source_origin"]) for entry in project["episodes"]] == [
        (1, "own"),
        (2, "own"),
    ]
    pm = ProjectManager(project_dir.parent.parent)
    summary = WorkflowStateService(pm).get_project_summary(project_dir.name)
    assert [episode.episode for episode in summary.episodes] == [1, 2]
    assert summary.source_remaining is False
    status = WorkflowStateService(pm).get_status(project_dir.name, 1)
    assert status.operations["plan_episodes"].reason == "whole_source_missing"
    assert status.operations["prepare_script_plan"].state == "admitted"
    assert status.next_action.type == "prepare_script_plan"


def test_unregistered_file_added_after_migration_changes_no_episode_source(tmp_path: Path) -> None:
    project_dir, before = _migrated_episode_sources(tmp_path, pre_split=False)
    pm = ProjectManager(project_dir.parent.parent)
    status_before = WorkflowStateService(pm).get_status(project_dir.name, 7)

    (project_dir / "source" / "第0章.txt").write_text("迁移后直接放进来的文件。", encoding="utf-8")
    (project_dir / "source" / "episode_3.txt").write_text("没有登记的集文件。", encoding="utf-8")

    after = pm.load_project(project_dir.name)
    assert after["episodes"] == before["episodes"]
    assert after["whole_source_files"] == before["whole_source_files"]
    status_after = WorkflowStateService(pm).get_status(project_dir.name, 7)
    assert status_after.source_revision == status_before.source_revision
    assert status_after.content == status_before.content


def test_legacy_split_episodes_stay_cut_episodes_and_only_a_full_reset_releases_them(tmp_path: Path) -> None:
    project_dir, project = _migrated_episode_sources(tmp_path, legacy_split=True)

    assert [(entry["episode"], entry["source_origin"]) for entry in project["episodes"]] == [
        (1, "whole_source"),
        (2, "whole_source"),
        (3, "none"),
        (7, "own"),
    ]
    assert not (project_dir / "source" / "snapshots").exists()
    pm = ProjectManager(project_dir.parent.parent)
    status = WorkflowStateService(pm).get_status(project_dir.name, 1)
    assert status.content is not None
    assert status.content.episode_source == "present"
    assert status.operations["prepare_script_plan"].state == "admitted"

    with pytest.raises(EpisodeResetError, match="source_range"):
        reset_episode_planning(project_dir, episode_id=2)
    assert _read_json(project_dir / "project.json")["episodes"] == project["episodes"]

    reset_episode_planning(project_dir)
    after = _read_json(project_dir / "project.json")
    assert [(entry["episode"], entry["source_origin"]) for entry in after["episodes"]] == [(3, "none"), (7, "own")]
    assert (project_dir / "source" / "_episode_1.txt.bak").is_file()
    assert (project_dir / "source" / "episode_7.txt").read_text(encoding="utf-8") == "另放进来的一集。"


def test_source_edited_outside_the_service_gets_no_snapshot(tmp_path: Path) -> None:
    project_dir, project = _migrated_episode_sources(tmp_path, edited_outside=True)

    assert not source_snapshot_path(project_dir, "source/第10章.txt").exists()
    assert project["source_fingerprints"] == {
        "source/第10章.txt": hashlib.sha256(LEGACY_CHAPTER_TEN.encode("utf-8")).hexdigest()
    }
