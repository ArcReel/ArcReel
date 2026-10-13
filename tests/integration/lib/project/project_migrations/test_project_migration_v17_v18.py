"""v17→v18 迁移：剪辑时间线修订删去 Agent 轮次字段；补录此前因生成缺口跳过的产物。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_currency import (
    ArtifactCurrencyResolver,
    resolve_current_artifact_basis,
    resolve_current_artifact_target,
)
from lib.artifacts.artifact_manifest import MANIFEST_FILENAME, ArtifactKey
from lib.artifacts.artifact_planner import plan_artifact_target_state_by_gap_rule
from lib.edit_timeline import EditTimelineService
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import load_migration_report
from lib.project.project_migrations import CURRENT_SCHEMA_VERSION
from lib.project.project_migrations.runner import cleanup_stale_backups, migrate_project_dir
from lib.speech.speech_composition import SpeechProblemCode, admit_script_unit
from tests.legacy_project_shapes import (
    advance_project_schema,
    write_legacy_drama_storyboard_project,
    write_legacy_presentation_project,
    write_legacy_reference_video_project,
    write_legacy_storyboard_project,
    write_legacy_timeline_agent_turn_project,
    write_legacy_unregistrable_asset_sheet_project,
    write_undescribed_style_bases_project,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


async def test_schema_17_timeline_with_agent_turn_reads_after_migration(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_dir = write_legacy_timeline_agent_turn_project(projects_root)
    timeline_path = project_dir / "edit_timelines" / "episode_1" / "tl-0000abcd.json"
    legacy = _read_json(timeline_path)
    assert _read_json(project_dir / "project.json")["schema_version"] == 17

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_SCHEMA_VERSION
    migrated = _read_json(timeline_path)
    assert migrated == {
        **legacy,
        "revisions": [
            {key: value for key, value in revision.items() if key != "agent_turn"} for revision in legacy["revisions"]
        ],
    }
    assert [path.name for path in timeline_path.parent.glob("tl-0000abcd.json.bak.v17-*")]

    service = EditTimelineService(ProjectManager(tmp_path))
    (summary,) = await service.list_timelines(project_dir.name)
    assert (summary.id, summary.revision, summary.update_summary) == ("tl-0000abcd", 2, "调整音量")
    history = await service.list_revisions(project_dir.name, "tl-0000abcd")
    assert [(item.number, item.author.kind) for item in history.revisions] == [(1, "arcreel_agent"), (2, "creator")]


def test_stale_timeline_backup_is_reclaimed_after_its_timeline_is_deleted(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_dir = write_legacy_timeline_agent_turn_project(projects_root)
    timeline_path = project_dir / "edit_timelines" / "episode_1" / "tl-0000abcd.json"
    assert migrate_project_dir(project_dir) is True
    (backup,) = timeline_path.parent.glob("tl-0000abcd.json.bak.v17-*")
    timeline_path.unlink()
    os.utime(backup, (0, 0))

    cleanup_stale_backups(projects_root)

    assert not backup.exists()


def test_symlinked_timeline_directories_outside_project_are_left_untouched(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_dir = write_legacy_timeline_agent_turn_project(projects_root)
    episode_dir = project_dir / "edit_timelines" / "episode_1"
    external_dir = tmp_path / "external-timelines"
    episode_dir.rename(external_dir)
    try:
        episode_dir.symlink_to(external_dir, target_is_directory=True)
        (project_dir / "edit_timelines" / "episode_2").mkdir()
        (project_dir / "edit_timelines" / "episode_2" / "tl-linked.json").symlink_to(external_dir / "tl-0000abcd.json")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable on this platform")
    external_file = external_dir / "tl-0000abcd.json"
    before = external_file.read_bytes()

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_SCHEMA_VERSION
    assert external_file.read_bytes() == before
    assert sorted(path.name for path in external_dir.iterdir()) == ["tl-0000abcd.json"]
    assert not list((project_dir / "edit_timelines").rglob("*.bak.v17-*"))


def _manifest_entries(project_dir: Path) -> dict[str, Any]:
    return _read_json(project_dir / MANIFEST_FILENAME)["entries"]


def test_artifacts_skipped_for_generation_input_gaps_are_backfilled_without_touching_registered_entries(
    tmp_path: Path,
) -> None:
    """v17 项目里空描述的场景图没有登记：这一步只补录它，已登记条目逐字节不变，报告不再列它。"""
    project_dir = write_legacy_unregistrable_asset_sheet_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=17)
    scene_key = ArtifactKey.asset_sheet("scene", "祠堂").encode()
    before = _manifest_entries(project_dir)
    assert scene_key not in before

    assert migrate_project_dir(project_dir) is True

    after = _manifest_entries(project_dir)
    assert {key: after[key] for key in before} == before
    assert after[scene_key]["artifact_path"] == "scenes/祠堂.png"
    resolver = ArtifactCurrencyResolver(project_dir)
    for key in set(after) - set(before):
        comparison = resolver.compare(ArtifactKey.decode(key), artifact_path=after[key]["artifact_path"])
        assert comparison.status.value == "current", key
    report = load_migration_report(project_dir)
    assert report is not None
    assert [item for item in report.skipped if item.resource_id == "祠堂"] == []
    assert list(project_dir.glob(f"{MANIFEST_FILENAME}.bak.v17-*"))


def test_a_mixed_speech_stock_video_is_registered_but_its_unit_still_needs_replanning(tmp_path: Path) -> None:
    """说书片段同时写了原文和角色台词：存量视频登记为 current，单位仍报混合发声、生成仍被拒。"""
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["segments"][0]["video_prompt"]["dialogue"] = [{"speaker": "张三", "line": "别回头。"}]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    assert migrate_project_dir(project_dir) is True

    video = ArtifactKey.episode_video(1, "E1S1")
    comparison = ArtifactCurrencyResolver(project_dir).compare(video, artifact_path="videos/scene_E1S1.mp4")
    assert comparison.status.value == "current"
    report = load_migration_report(project_dir)
    assert report is not None
    assert [item for item in report.skipped if item.resource_id == "E1S1" and item.kind == "video"] == []
    segment = _read_json(script_path)["segments"][0]
    admission = admit_script_unit("segments", segment)
    assert not admission.allowed
    assert [problem.code for problem in admission.preparation.problems] == [SpeechProblemCode.MIXED_SPEECH]


@pytest.mark.parametrize(
    "write_project",
    [
        write_legacy_storyboard_project,
        write_legacy_drama_storyboard_project,
        write_legacy_reference_video_project,
        write_legacy_presentation_project,
    ],
)
def test_projects_without_generation_input_gaps_plan_identically_under_both_gap_rules(
    tmp_path: Path, write_project: Any
) -> None:
    """输入完整的项目两种缺口口径规划出同一份目标态：这一步不碰它们的清单。"""
    project_dir = write_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=17)
    before = _manifest_entries(project_dir)
    project = _read_json(project_dir / "project.json")

    refused = plan_artifact_target_state_by_gap_rule(project_dir, project, record_gaps=False)
    recorded = plan_artifact_target_state_by_gap_rule(project_dir, project, record_gaps=True)

    assert recorded.entries == refused.entries
    assert migrate_project_dir(project_dir) is True
    assert _manifest_entries(project_dir) == before


def test_a_grid_whose_reference_sheet_is_gone_is_backfilled_with_the_reference_recorded_absent(tmp_path: Path) -> None:
    """宫格记录冻结的参考图已读不到，原先联合图与切格分镜都没登记：补录后读作 current，依据记下缺席的参考。"""
    project_dir = write_undescribed_style_bases_project(tmp_path / "projects", "grid", route="grid")
    advance_project_schema(project_dir, to_version=17)
    grid_path = project_dir / "grids" / "grid_123456789abc.json"
    grid = _read_json(grid_path)
    grid["reference_images"] = [{"path": "characters/已删除.png", "name": "已删除", "ref_type": "character"}]
    grid_path.write_text(json.dumps(grid, ensure_ascii=False), encoding="utf-8")
    grid_key = ArtifactKey.episode_grid(1, "grid_123456789abc")
    members = [ArtifactKey.episode_storyboard(1, resource_id) for resource_id in ("E1S01", "E1S02")]
    manifest_path = project_dir / MANIFEST_FILENAME
    manifest = _read_json(manifest_path)
    for key in (grid_key, *members):
        manifest["entries"].pop(key.encode())
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    assert migrate_project_dir(project_dir) is True

    resolver = ArtifactCurrencyResolver(project_dir)
    entries = _manifest_entries(project_dir)
    for key in (grid_key, *members):
        comparison = resolver.compare(key, artifact_path=entries[key.encode()]["artifact_path"])
        assert comparison.status.value == "current", key
    basis = resolve_current_artifact_basis(project_dir, grid_key)
    assert basis is not None
    assert json.loads(basis.normalized_bytes())["inputs"]["absent"] == [
        {"code": "reference_asset_missing", "type": "character", "id": "已删除"}
    ]


def test_turning_a_presented_unit_into_mixed_speech_reads_its_media_layers_stale(tmp_path: Path) -> None:
    """已登记的单位改成混合发声：字幕与呈现模型的当前依据照常投影（准入问题记为缺席），全部读作 stale。"""
    project_dir = write_legacy_presentation_project(tmp_path / "projects")
    assert migrate_project_dir(project_dir) is True
    keys = (
        ArtifactKey.episode_video(1, "E1S01"),
        ArtifactKey.episode_subtitle(1, "E1S01", "post_production"),
        ArtifactKey.episode_presentation(1, "E1S01", "post_production"),
    )
    entries = _manifest_entries(project_dir)
    resolver = ArtifactCurrencyResolver(project_dir)
    for key in keys:
        assert resolver.compare(key, artifact_path=entries[key.encode()]["artifact_path"]).status.value == "current"
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["segments"][0]["video_prompt"] = {
        "action": "阿离转身",
        "camera_motion": "Static",
        "dialogue": [{"speaker": "阿离", "line": "别回头。"}],
    }
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resolver = ArtifactCurrencyResolver(project_dir)
    for key in keys:
        assert resolver.compare(key, artifact_path=entries[key.encode()]["artifact_path"]).status.value == "stale", key
    for key in keys[1:]:
        target = resolve_current_artifact_target(project_dir, key)
        assert target is not None, key
        assert target.basis_digest != entries[key.encode()]["basis_digest"]
