from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactManifestEntry,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import load_migration_report
from lib.project.project_migrations import CURRENT_SCHEMA_VERSION
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_migrations.v15_to_v16_edit_decisions import migrate_v15_to_v16
from lib.speech.narration_config import ProjectTtsSettingsResolver, TtsSynthesisSettings
from lib.speech.narration_delivery import USE_TTS, NarrationTtsStatus, prepare_current_narration_delivery
from lib.speech.speech_composition import admit_script_unit
from lib.speech.speech_presentation import presentation_artifact_paths
from lib.workflow.workflow_state import WorkflowStateService
from server.services.presentation.presentation_read_model import PresentationReadModelService
from tests.legacy_project_shapes import (
    advance_project_schema,
    legacy_transition_presentation_basis,
    write_legacy_ad_reference_video_project,
    write_legacy_presentation_project,
    write_legacy_reference_video_project,
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


async def _tts_status(project_dir: Path, unit_id: str) -> NarrationTtsStatus:
    """用户在旁白配音面板上看到的时效：按迁移后项目的 TTS 快照复算。"""

    async def _duration(_path: Path) -> float:
        return 3.0

    project = _read_json(project_dir / "project.json")
    script = _read_json(project_dir / "scripts" / "episode_1.json")
    segment = next(item for item in script["segments"] if item["segment_id"] == unit_id)
    prepared = await prepare_current_narration_delivery(
        project=project,
        episode=1,
        preparation=admit_script_unit("segments", segment).preparation,
        project_path=project_dir,
        delivery=USE_TTS,
        resolver=ProjectTtsSettingsResolver(),
        duration_probe=_duration,
    )
    return prepared.tts_status


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
    assert await _tts_status(project_dir, "E1S1") is NarrationTtsStatus.CURRENT
    assert await _tts_status(project_dir, "E1S2") is NarrationTtsStatus.CURRENT

    async with db_factory() as session:
        service = ConfigService(session)
        await service.set_setting("default_audio_backend", "dashscope/qwen-tts-latest")
        await service.set_setting("narration_voice", "Cherry")
        await service.set_setting("narration_speed", "0.8")
        await session.commit()
    defaults, voice, speed = await ConfigResolver(db_factory).default_narration_tts()
    assert (defaults.model_id, voice, speed) == ("qwen-tts-latest", "Cherry", 0.8)
    assert _read_json(project_dir / "project.json") == project
    assert await _tts_status(project_dir, "E1S1") is NarrationTtsStatus.CURRENT

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
        assert await _tts_status(project_dir, "E1S1") is NarrationTtsStatus.CURRENT
        assert await _tts_status(project_dir, "E1S2") is NarrationTtsStatus.CURRENT
        summary = WorkflowStateService(manager).get_project_summary(project_dir.name)
        assert (summary.episodes[0].videos.available, summary.episodes[0].videos.stale) == (2, 0)


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

    project = _read_json(project_dir / "project.json")
    assert project["narration_delivery"] == "use_tts"
    assert (project["audio_backend"], project["narration_voice"]) == ("openai/tts-1", "alloy")
    # 快照不设语速：旧项目里「跟随全局默认」的语速字段不再保留
    assert "narration_speed" not in project
    assert await _tts_status(project_dir, "E1S2") is NarrationTtsStatus.CURRENT
    assert await _tts_status(project_dir, "E1S1") is NarrationTtsStatus.STALE


def test_project_without_registered_narration_audio_becomes_post_production(tmp_path: Path) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=15)
    legacy = _read_json(project_dir / "project.json")
    legacy.update({"audio_backend": "dashscope", "narration_voice": "Cherry"})
    _write_json(project_dir / "project.json", legacy)

    migrate_v15_to_v16(project_dir)

    project = _read_json(project_dir / "project.json")
    assert project == {**legacy, "narration_delivery": "post_production", "schema_version": 16}


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
