"""迁移报告：只记调整与没能带入的内容，确认过、未完成或为空的报告不再给出。"""

from __future__ import annotations

import json
from pathlib import Path

from lib.project.project_migration_report import (
    MIGRATION_REPORT_FILENAME,
    ArtifactBackfillOutcome,
    MigrationAdjustment,
    MigrationSkippedArtifact,
    acknowledge_migration_report,
    build_migration_report,
    extend_migration_report,
    load_migration_report,
    visible_migration_report,
    write_migration_report,
)

_PLACEHOLDER = MigrationAdjustment(code="placeholder_asset_added", params={"asset_type": "scene", "name": "雨巷"})
_SKIP = MigrationSkippedArtifact(
    kind="episode-audio",
    episode=1,
    resource_id="E1S01",
    artifact_path="audio/segment_E1S01.wav",
    reason="legacy",
    category="legacy_audio_basis",
)


def _write(project_dir: Path, *, adjusted=(), skipped=(), complete: bool = True) -> None:
    write_migration_report(
        project_dir,
        build_migration_report(
            ArtifactBackfillOutcome(registered={}, skipped=tuple(skipped)),
            from_schema_version=14,
            to_schema_version=18,
            adjusted=adjusted,
            complete=complete,
        ),
    )


def test_a_report_with_items_is_visible_until_acknowledged(tmp_path: Path) -> None:
    _write(tmp_path, adjusted=[_PLACEHOLDER], skipped=[_SKIP])

    visible = visible_migration_report(tmp_path)
    assert visible is not None
    assert visible.item_count == 2

    assert acknowledge_migration_report(tmp_path) is True
    assert visible_migration_report(tmp_path) is None
    assert acknowledge_migration_report(tmp_path) is False


def test_an_empty_or_unfinished_report_is_not_visible(tmp_path: Path) -> None:
    _write(tmp_path)
    assert visible_migration_report(tmp_path) is None

    _write(tmp_path, adjusted=[_PLACEHOLDER], complete=False)
    assert visible_migration_report(tmp_path) is None


def test_a_report_in_the_earlier_format_reads_as_absent(tmp_path: Path) -> None:
    (tmp_path / MIGRATION_REPORT_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "migrated_at": "2026-01-01T00:00:00Z",
                "from_schema_version": 7,
                "to_schema_version": 17,
                "registered": {},
                "skipped": [],
            }
        ),
        encoding="utf-8",
    )

    assert load_migration_report(tmp_path) is None


def test_items_added_after_the_chain_join_the_report_once(tmp_path: Path) -> None:
    extend_migration_report(tmp_path, adjusted=[_PLACEHOLDER], skipped=[_SKIP])
    extend_migration_report(tmp_path, adjusted=[_PLACEHOLDER], skipped=[_SKIP])

    report = load_migration_report(tmp_path)
    assert report is not None
    assert report.adjusted == [_PLACEHOLDER]
    assert report.skipped == [_SKIP]


def test_new_items_make_an_acknowledged_report_visible_again(tmp_path: Path) -> None:
    _write(tmp_path, adjusted=[_PLACEHOLDER])
    acknowledge_migration_report(tmp_path)

    extend_migration_report(tmp_path, adjusted=[_PLACEHOLDER])
    assert visible_migration_report(tmp_path) is None

    extend_migration_report(tmp_path, skipped=[_SKIP])
    visible = visible_migration_report(tmp_path)
    assert visible is not None
    assert visible.item_count == 2


def test_a_basis_that_cannot_be_computed_is_not_shown(tmp_path: Path) -> None:
    """报告只给用户看读不出的文件与旧版旁白音频；依据算不出的跳过只记账，写盘时去掉。"""
    gap = MigrationSkippedArtifact(
        kind="episode-video", episode=1, resource_id="E1S01", artifact_path="videos/scene_E1S01.mp4", reason="gap"
    )
    unreadable = gap.model_copy(update={"kind": "episode-storyboard", "category": "unreadable"})

    _write(tmp_path, skipped=[gap, unreadable])
    extend_migration_report(tmp_path, skipped=[gap])

    report = load_migration_report(tmp_path)
    assert report is not None
    assert report.skipped == [unreadable]


def test_adding_nothing_writes_nothing(tmp_path: Path) -> None:
    extend_migration_report(tmp_path)

    assert not (tmp_path / MIGRATION_REPORT_FILENAME).exists()


def test_media_of_a_unit_that_needs_replanning_is_left_out_of_the_report(tmp_path: Path) -> None:
    """需要重新规划的条目在界面上另有标记，它的产物不进「未能带入」，不论哪条路径写报告。"""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "project.json").write_text(
        json.dumps({"episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}]}), encoding="utf-8"
    )
    (tmp_path / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "video_units": [{"unit_id": "E1S01", "needs_replan": True}, {"unit_id": "E1S02"}],
            }
        ),
        encoding="utf-8",
    )
    other = _SKIP.model_copy(update={"resource_id": "E1S02", "artifact_path": "audio/segment_E1S02.wav"})

    _write(tmp_path, skipped=[_SKIP, other])

    report = load_migration_report(tmp_path)
    assert report is not None
    assert report.skipped == [other]
