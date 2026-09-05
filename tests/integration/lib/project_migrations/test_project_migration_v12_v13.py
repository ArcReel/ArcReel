"""v12→v13：为旧视频版本记录补写类型化来源、无计划剧本按固定依据登记、迁移报告落盘。"""

from __future__ import annotations

import json
from pathlib import Path

from lib.artifact_activation import ArtifactCurrencyResolver
from lib.artifact_manifest import ArtifactKey, ArtifactStatus, compose_video_artifact_basis
from lib.artifact_version_provenance import parse_typed_media_version_target
from lib.project_manager import ProjectManager
from lib.project_migration_report import MIGRATION_REPORT_FILENAME, load_migration_report
from lib.project_migrations.runner import migrate_project_dir
from lib.project_migrations.v12_to_v13_legacy_media_provenance import migrate_v12_to_v13
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.speech_artifact_provenance import build_video_duration_basis, build_video_speech_basis
from lib.speech_composition import admit_script_unit
from lib.version_manager import VersionManager
from lib.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.visual_artifact_provenance import build_storyboard_video_artifact_visual_basis
from lib.workflow_state import WorkflowStateService
from tests.legacy_project_shapes import (
    advance_project_schema,
    write_legacy_reference_video_project,
    write_legacy_storyboard_project,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _selected_record(project_dir: Path, resource_type: str, resource_id: str) -> dict:
    bucket = _read_json(project_dir / "versions" / "versions.json")[resource_type][resource_id]
    return next(record for record in bucket["versions"] if record["version"] == bucket["current_version"])


def _video_state(project_dir: Path, resource_type: str, resource_id: str) -> str:
    resolver = ArtifactCurrencyResolver(project_dir)
    artifact_path = (
        f"{resource_type}/scene_{resource_id}.mp4"
        if resource_type == "videos"
        else f"{resource_type}/{resource_id}.mp4"
    )
    return resolver.compare(ArtifactKey.episode_video(1, resource_id), artifact_path=artifact_path).status.value


def _script_state(project_dir: Path) -> str:
    resolver = ArtifactCurrencyResolver(project_dir)
    return resolver.compare(ArtifactKey.episode_script(1), artifact_path="scripts/episode_1.json").status.value


def test_legacy_storyboard_videos_become_current_after_provenance_backfill(tmp_path: Path) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=12)

    outcome = migrate_v12_to_v13(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == 13
    assert outcome is not None
    assert outcome.registered["episode-video"] == 2
    assert outcome.skipped == ()
    for resource_id in ("E1S1", "E1S2"):
        record = _selected_record(project_dir, "videos", resource_id)
        target = parse_typed_media_version_target("videos", record)
        assert target.episode == 1
        assert target.script_file == "episode_1.json"
        assert isinstance(record["provenance_backfilled_at"], str)
        assert record["provenance_backfilled_at"]
        assert _video_state(project_dir, "videos", resource_id) == ArtifactStatus.CURRENT.value


def test_legacy_reference_videos_become_current_and_legacy_audio_is_reported(tmp_path: Path) -> None:
    project_dir = write_legacy_reference_video_project(tmp_path / "projects", with_legacy_audio=True)
    advance_project_schema(project_dir, to_version=12)

    outcome = migrate_v12_to_v13(project_dir)

    assert outcome is not None
    assert outcome.registered["episode-video"] == 2
    for resource_id in ("E1U01", "E1U02"):
        assert _video_state(project_dir, "reference_videos", resource_id) == ArtifactStatus.CURRENT.value
    skipped = {(item.kind, item.resource_id): item.reason for item in outcome.skipped}
    assert set(skipped) == {("episode-audio", "E1U01"), ("episode-audio", "E1U02")}
    assert all("TTS" in reason for reason in skipped.values())
    audio_record = _selected_record(project_dir, "audio", "E1U01")
    assert "provenance_backfilled_at" not in audio_record


def test_summary_counts_legacy_videos_and_reports_the_script_generated(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = write_legacy_reference_video_project(root)
    advance_project_schema(project_dir, to_version=12)
    migrate_v12_to_v13(project_dir)

    summary = WorkflowStateService(ProjectManager(root)).get_project_summary(project_dir.name)

    episode = summary.episodes[0]
    assert episode.script_status == "generated"
    assert episode.item_count == 2
    assert (episode.videos.total, episode.videos.available, episode.videos.stale) == (2, 2, 0)
    assert summary.phase != "preparation"


def test_status_passes_the_script_plan_gate_for_a_registered_planless_script(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = write_legacy_storyboard_project(root)
    advance_project_schema(project_dir, to_version=12)
    migrate_v12_to_v13(project_dir)

    status = WorkflowStateService(ProjectManager(root)).get_status(project_dir.name, 1)

    assert status.state not in {"SCRIPT_PLAN_CONTENT", "SCRIPT_PLAN_REVIEW", "FINAL_SCRIPT"}
    assert status.artifacts["script"]["state"] == ArtifactStatus.CURRENT.value
    assert status.artifacts["videos"]["current_ids"] == ["E1S1", "E1S2"]


def test_planless_script_stays_current_until_a_formal_plan_appears(tmp_path: Path) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=12)
    migrate_v12_to_v13(project_dir)
    assert _script_state(project_dir) == ArtifactStatus.CURRENT.value

    (project_dir / "source" / "episode_1.txt").write_text("第一段旁白。第二段旁白。", encoding="utf-8")
    (project_dir / "drafts" / "episode_1" / "script_plan_segments.json").write_text(
        json.dumps({"segments": [{"novel_text": "第一段旁白。"}]}, ensure_ascii=False), encoding="utf-8"
    )

    assert _script_state(project_dir) == ArtifactStatus.STALE.value


def test_typed_records_are_left_untouched_and_the_migration_is_idempotent(tmp_path: Path) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path / "projects", unit_ids=("E1S1",))
    advance_project_schema(project_dir, to_version=12)
    script = _read_json(project_dir / "scripts" / "episode_1.json")
    item = script["segments"][0]
    preparation = admit_script_unit("segments", item).preparation
    visual = build_storyboard_video_artifact_visual_basis(
        resource_id="E1S1",
        visual_prompt=item["video_prompt"],
        storyboard_image=project_dir / "storyboards" / "scene_E1S1.png",
        end_frame_image=None,
        aspect_ratio="9:16",
    )
    speech = build_video_speech_basis(preparation)
    duration = build_video_duration_basis(4)
    facts = VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=4,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(4, 8),
        reference_image_limit=None,
        parent_version=1,
    )
    VersionManager(project_dir).add_version(
        "videos",
        "E1S1",
        "typed",
        source_file=project_dir / "videos" / "scene_E1S1.mp4",
        execution_checkpoint_schema_version=3,
        execution_script_file="episode_1.json",
        execution_duration_seconds=4,
        execution_request_digest="d" * 64,
        execution_provider_media=[],
        artifact_video_currency=facts.to_dict(),
    )
    before = _selected_record(project_dir, "videos", "E1S1")

    outcome = migrate_v12_to_v13(project_dir)

    assert outcome is not None
    assert outcome.registered["episode-video"] == 1
    assert _selected_record(project_dir, "videos", "E1S1") == before
    assert migrate_v12_to_v13(project_dir) is None
    assert _read_json(project_dir / "project.json")["schema_version"] == 13


def test_video_without_projectable_basis_is_skipped_and_reported_without_blocking(tmp_path: Path) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=12)
    (project_dir / "storyboards" / "scene_E1S2.png").unlink()

    outcome = migrate_v12_to_v13(project_dir)

    assert outcome is not None
    assert _read_json(project_dir / "project.json")["schema_version"] == 13
    assert outcome.registered["episode-video"] == 1
    assert [(item.kind, item.resource_id) for item in outcome.skipped] == [("episode-video", "E1S2")]
    assert "storyboard" in outcome.skipped[0].reason
    assert "provenance_backfilled_at" not in _selected_record(project_dir, "videos", "E1S2")


def test_full_chain_from_schema7_writes_a_migration_report_exposed_on_status(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = write_legacy_reference_video_project(root, with_legacy_audio=True)

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    report = load_migration_report(project_dir)
    assert report is not None
    assert (project_dir / MIGRATION_REPORT_FILENAME).is_file()
    assert (report.from_schema_version, report.to_schema_version) == (7, CURRENT_PROJECT_SCHEMA_VERSION)
    assert report.registered["episode-video"] == 2
    assert report.registered["episode-script"] == 1
    assert sorted(item.resource_id for item in report.skipped) == ["E1U01", "E1U02"]
    assert len(list((project_dir / "versions").glob("versions.json.bak.v12-*"))) == 1

    status = WorkflowStateService(ProjectManager(root)).get_status(project_dir.name, 1)
    assert status.migration_report == report


def test_media_on_a_needs_replan_unit_is_reported_instead_of_dropped(tmp_path: Path) -> None:
    project_dir = write_legacy_reference_video_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=12)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["video_units"][1]["needs_replan"] = True
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    outcome = migrate_v12_to_v13(project_dir)

    assert outcome is not None
    assert outcome.registered["episode-video"] == 1
    assert [(item.kind, item.resource_id) for item in outcome.skipped] == [("episode-video", "E1U02")]
    assert "needs_replan" in outcome.skipped[0].reason
