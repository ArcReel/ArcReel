"""过去正常流程写出的旧形态与单件损坏不再拒绝整个项目：迁移链规整或跳过，原内容留在盘上，报告列出改动（ADR 0098）。

样本取自 ``tests/legacy_project_shapes.py``，形态清单见 ``docs/agents/project-migrations.md``「已知旧形态」。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import acknowledge_migration_report, load_migration_report
from lib.project.project_migrations.runner import migrate_project_with_verdict
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.workflow.workflow_state import WorkflowStateService
from tests.integration.lib.project.project_migrations.test_project_migration_v7_v8 import _project
from tests.legacy_project_shapes import (
    add_colliding_asset_name,
    add_duplicate_ledger_entry,
    blank_reference_unit_shots,
    break_script_item_ids,
    duplicate_legacy_ad_ids,
    reuse_item_id_across_episodes,
    set_script_episode,
    share_episode_script,
    write_legacy_ad_reference_video_project,
    write_legacy_reference_video_project,
    write_non_utf8_episode_source,
    write_unreadable_grid_record,
    write_unreadable_script_plan_draft,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _migrate(project_dir: Path) -> None:
    assert migrate_project_with_verdict(project_dir) is None
    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION


def _adjusted(project_dir: Path) -> list[tuple[str, int | None, dict[str, str]]]:
    report = load_migration_report(project_dir)
    assert report is not None
    assert report.complete
    return [(item.code, item.episode, item.params) for item in report.adjusted]


def _skipped(project_dir: Path) -> list[tuple[str, str]]:
    report = load_migration_report(project_dir)
    assert report is not None
    return [(item.kind, item.artifact_path) for item in report.skipped]


def test_a_duplicate_ledger_entry_is_dropped_and_its_script_stays_on_disk(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    add_duplicate_ledger_entry(project_dir, 1, "episode_1_copy.json")
    original = (project_dir / "project.json").read_bytes()

    _migrate(project_dir)

    episodes = _read_json(project_dir / "project.json")["episodes"]
    assert [(entry["episode"], entry["script_file"]) for entry in episodes] == [(1, "scripts/episode_1.json")]
    assert (project_dir / "scripts" / "episode_1_copy.json").is_file()
    report = load_migration_report(project_dir)
    assert report is not None
    [adjustment] = report.adjusted
    assert (adjustment.code, adjustment.episode, adjustment.file) == (
        "duplicate_episode_entry_removed",
        1,
        "scripts/episode_1_copy.json",
    )
    assert adjustment.backup is not None
    assert (project_dir / adjustment.backup).read_bytes() == original


def test_a_script_shared_by_two_episodes_stays_with_the_episode_it_names(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    share_episode_script(project_dir, 1, with_episode=2)
    set_script_episode(project_dir, 1, 2)
    script_before = (project_dir / "scripts" / "episode_1.json").read_bytes()

    _migrate(project_dir)

    episodes = _read_json(project_dir / "project.json")["episodes"]
    assert [(entry["episode"], entry["script_file"]) for entry in episodes] == [
        (1, "scripts/episode_1.json"),
        (2, "scripts/episode_2.json"),
    ]
    assert not (project_dir / "scripts" / "episode_1.json").exists()
    assert _read_json(project_dir / "scripts" / "episode_2.json") == json.loads(script_before)
    assert [(code, episode) for code, episode, _params in _adjusted(project_dir)] == [
        ("shared_script_binding_split", 1),
        ("script_binding_canonicalized", 2),
    ]


def test_a_script_without_its_episode_number_takes_the_one_it_is_bound_to(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    set_script_episode(project_dir, 1, None)

    _migrate(project_dir)

    assert _read_json(project_dir / "scripts" / "episode_1.json")["episode"] == 1
    assert _adjusted(project_dir) == [("script_episode_corrected", 1, {"from": "", "to": "1"})]


def test_asset_names_that_collide_after_normalization_keep_the_first(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    colliding = add_colliding_asset_name(project_dir, "characters", "阿离")

    _migrate(project_dir)

    assert list(_read_json(project_dir / "project.json")["characters"]) == ["阿离"]
    assert _adjusted(project_dir) == [
        ("duplicate_asset_removed", None, {"asset_type": "character", "name": colliding, "kept": "阿离"})
    ]
    report = load_migration_report(project_dir)
    assert report is not None
    assert report.adjusted[0].backup is not None
    assert colliding in _read_json(project_dir / report.adjusted[0].backup)["characters"]


def test_missing_and_duplicate_item_ids_are_reassigned_and_the_original_media_stays_with_the_first(
    tmp_path: Path,
) -> None:
    project_dir, *_ = _project(tmp_path)
    break_script_item_ids(project_dir, 1)

    _migrate(project_dir)

    segments = _read_json(project_dir / "scripts" / "episode_1.json")["segments"]
    assert [segment["segment_id"] for segment in segments] == ["E1S01", "E1S02", "E1S03"]
    assert segments[0]["generated_assets"]["storyboard_image"] == "storyboards/scene_E1S01.png"
    assert segments[1]["generated_assets"]["storyboard_image"] is None
    assert segments[1]["image_prompt"] == "另一镜"
    assert _adjusted(project_dir) == [
        ("item_id_reassigned", 1, {"from": "E1S01", "to": "E1S02"}),
        ("item_id_reassigned", 1, {"from": "", "to": "E1S03"}),
    ]
    assert (project_dir / "storyboards" / "scene_E1S01.png").is_file()


@pytest.mark.parametrize(
    ("item_id", "owner", "renumbered_to"),
    [
        pytest.param("E2S01", 2, ("E1S01", 1), id="the-episode-the-id-prefix-names"),
        pytest.param("S01", 1, ("E2S01", 2), id="the-first-episode-in-the-ledger"),
    ],
)
def test_an_item_id_two_episodes_share_stays_with_one_and_is_renumbered_in_the_other(
    tmp_path: Path, item_id: str, owner: int, renumbered_to: tuple[str, int]
) -> None:
    project_dir, *_ = _project(tmp_path)
    reuse_item_id_across_episodes(project_dir, item_id)

    _migrate(project_dir)

    new_id, other = renumbered_to
    owned = _read_json(project_dir / "scripts" / f"episode_{owner}.json")["segments"][0]
    moved = _read_json(project_dir / "scripts" / f"episode_{other}.json")["segments"][0]
    assert (owned["segment_id"], owned["generated_assets"]["storyboard_image"]) == (
        item_id,
        f"storyboards/scene_{item_id}.png",
    )
    assert (moved["segment_id"], moved["generated_assets"]["storyboard_image"]) == (new_id, None)
    assert (project_dir / "storyboards" / f"scene_{item_id}.png").read_bytes() == b"shared storyboard"
    assert ("item_id_reassigned", other, {"from": item_id, "to": new_id}) in _adjusted(project_dir)
    entries = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    assert ArtifactKey.episode_storyboard(owner, item_id) in entries
    assert ArtifactKey.episode_storyboard(other, item_id) not in entries


def test_duplicate_ids_in_a_legacy_ad_shot_index_are_renumbered(tmp_path: Path) -> None:
    project_dir = write_legacy_ad_reference_video_project(tmp_path / "projects")
    duplicate_legacy_ad_ids(project_dir)

    _migrate(project_dir)

    units = _read_json(project_dir / "scripts" / "episode_1.json")["video_units"]
    assert units[0]["unit_id"] == "E1U01"
    assert "needs_replan" not in units[0]
    assert all(unit.get("needs_replan") is True for unit in units[1:])
    renumbered = [params["from"] for code, _episode, params in _adjusted(project_dir) if code == "item_id_reassigned"]
    assert sorted(renumbered) == ["E1S1", "E1U01"]


def test_a_reference_unit_whose_text_comes_out_blank_is_marked_for_replanning(tmp_path: Path) -> None:
    project_dir = write_legacy_reference_video_project(tmp_path / "projects", split_unit_text=True)
    blank_reference_unit_shots(project_dir, "E1U01")

    _migrate(project_dir)

    first, second = _read_json(project_dir / "scripts" / "episode_1.json")["video_units"]
    assert (first["text"], first["duration_seconds"], first["needs_replan"]) == ("", 0, True)
    assert second["text"] == "第2个单元的画面描述。"
    report = load_migration_report(project_dir)
    assert report is None or not [item for item in report.skipped if item.resource_id == "E1U01"]


def test_a_corrupt_grid_record_is_kept_as_is_and_reported(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    record = write_unreadable_grid_record(project_dir)

    _migrate(project_dir)

    assert record.read_bytes() == b"{not json"
    assert ("episode-grid", "grids/grid_0123456789ab.json") in _skipped(project_dir)


def test_a_corrupt_script_plan_draft_is_kept_as_is_and_reported(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    draft = write_unreadable_script_plan_draft(project_dir, 1, "script_plan_segments.json")

    _migrate(project_dir)

    assert draft.read_bytes() == b"{not json"
    assert ("episode-script-plan", "drafts/episode_1/script_plan_segments.json") in _skipped(project_dir)
    entries = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    assert ArtifactKey.episode_script(1) in entries


def test_a_source_that_is_not_utf8_leaves_the_script_plan_unregistered(tmp_path: Path) -> None:
    """草稿本身读得出、只是依据算不出：不登记、读时报 missing，不进「未能带入」。"""
    project_dir, *_ = _project(tmp_path)
    source = write_non_utf8_episode_source(project_dir, 1, "雨夜")

    _migrate(project_dir)

    assert source.read_bytes() == "雨夜".encode("gbk")
    report = load_migration_report(project_dir)
    assert report is None or not [item for item in report.skipped if item.kind == "episode-script-plan"]
    assert ArtifactKey.episode_script_plan(1) not in ProjectArtifactManifestAdapter(project_dir).snapshot_entries()


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions that the current user cannot bypass"
)
def test_an_unreadable_image_stays_in_place_and_is_reported(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    image = project_dir / "storyboards" / "scene_E1S01.png"
    image.chmod(0)
    try:
        if os.access(image, os.R_OK):
            pytest.skip("running with privileges that ignore file permissions")
        _migrate(project_dir)
    finally:
        image.chmod(0o644)

    assert image.read_bytes() == b"storyboard"
    assert ("episode-storyboard", "storyboards/scene_E1S01.png") in _skipped(project_dir)


def test_adjustments_survive_a_chain_that_fails_and_is_retried(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    add_duplicate_ledger_entry(project_dir, 1, "episode_1_copy.json")
    script_path = project_dir / "scripts" / "episode_1.json"
    script_bytes = script_path.read_bytes()
    # 剧本读不出仍拒绝整个项目：链停在半路，已做的规整写进未完成的报告。
    script_path.write_text('{"episode": 1, "segments": [', encoding="utf-8")

    assert migrate_project_with_verdict(project_dir) is not None
    unfinished = load_migration_report(project_dir)
    assert unfinished is not None
    assert not unfinished.complete
    assert [item.code for item in unfinished.adjusted] == ["duplicate_episode_entry_removed"]

    script_path.write_bytes(script_bytes)
    _migrate(project_dir)

    assert [code for code, *_ in _adjusted(project_dir)] == ["duplicate_episode_entry_removed"]


def test_the_status_shows_the_report_until_it_is_acknowledged(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    add_colliding_asset_name(project_dir, "characters", "阿离")
    _migrate(project_dir)
    service = WorkflowStateService(ProjectManager(str(tmp_path)))

    shown = service.get_status("demo").migration_report
    assert shown is not None
    assert [item.code for item in shown.adjusted] == ["duplicate_asset_removed"]

    assert acknowledge_migration_report(project_dir) is True
    assert service.get_status("demo").migration_report is None
