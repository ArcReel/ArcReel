"""v13→v14：遗留风格值归一后，既有产物不因这次清理翻过期，本就过期的不被伪造成时新。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.artifact_currency import ArtifactCurrencyResolver
from lib.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifact_planner import TargetStatePlanner
from lib.project_manager import ProjectManager
from lib.project_migration_report import load_migration_report
from lib.project_migrations.runner import migrate_project_dir
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.style_templates import resolve_template_prompt
from lib.workflow_state import WorkflowStateService
from tests.legacy_project_shapes import advance_project_schema, write_legacy_style_project

_NORMALIZED_STYLE = "写实电影感"
_PREFIXED_STYLE = f"画风：{_NORMALIZED_STYLE}"

_CHARACTER_SHEET = ArtifactKey.asset_sheet("character", "阿离")
_SCENE_SHEET = ArtifactKey.asset_sheet("scene", "雨巷")
_GRID = ArtifactKey.episode_grid(1, "grid_123456789abc")
_GRID_MEMBER = ArtifactKey.episode_storyboard(1, "E1S01")
_STORYBOARD = ArtifactKey.episode_storyboard(1, "E1S03")
_SCRIPT = ArtifactKey.episode_script(1)


def _entries(project_dir: Path) -> Mapping[ArtifactKey, ArtifactManifestEntry]:
    return ProjectArtifactManifestAdapter(project_dir).snapshot_entries()


def _status(project_dir: Path, key: ArtifactKey) -> str:
    entry = _entries(project_dir)[key]
    return ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=entry.artifact_path).status.value


def _is_current_before_migration(project_dir: Path, key: ArtifactKey) -> bool:
    """迁移前的时新性判定。读模型只服务当前 schema 的项目，此处直接比对登记与目标态。"""
    return _entries(project_dir).get(key) == TargetStatePlanner(project_dir).plan().entries.get(key)


def _read_project(project_dir: Path) -> dict:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


def _write_project(project_dir: Path, project: dict) -> None:
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")


def _legacy_project_at_v13(root: Path, *, style: str = _PREFIXED_STYLE, name: str = "legacy") -> Path:
    """一个停在 v13、产物齐全的遗留风格项目，单张分镜图的依据按归一后的风格值登记。

    单张分镜图的依据在风格值进入摘要前先剥前缀，存量清单里它的摘要因此已是归一形态；风格值
    已归一的孪生项目算出的登记与之逐字相同，用它补上这一事实。资产图、宫格（含宫格切分出的
    分镜图）与剧本的依据记的是带前缀的原始值，保持构造器写出的形状。
    """

    project_dir = write_legacy_style_project(root, name, style=style)
    advance_project_schema(project_dir, to_version=13)
    twin = write_legacy_style_project(root, f"{name}-twin", style=_NORMALIZED_STYLE)
    advance_project_schema(twin, to_version=13)
    ProjectArtifactManifestAdapter(project_dir).put_entry(_STORYBOARD, _entries(twin)[_STORYBOARD])
    return project_dir


def test_prefixed_style_is_normalized_across_the_whole_chain(tmp_path: Path) -> None:
    project_dir = write_legacy_style_project(tmp_path / "projects", style=_PREFIXED_STYLE)

    assert migrate_project_dir(project_dir) is True

    project = _read_project(project_dir)
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert project["style"] == _NORMALIZED_STYLE
    report = load_migration_report(project_dir)
    assert report is not None
    assert report.registered["asset-sheet"] == 2


def test_legacy_short_label_resolves_to_its_template_across_the_whole_chain(tmp_path: Path) -> None:
    project_dir = write_legacy_style_project(tmp_path / "projects", style="Photographic")

    assert migrate_project_dir(project_dir) is True

    project = _read_project(project_dir)
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert project["style_template_id"] == "live_premium_drama"
    assert project["style"] == resolve_template_prompt("live_premium_drama")


def test_already_normalized_project_keeps_every_basis_digest(tmp_path: Path) -> None:
    project_dir = write_legacy_style_project(tmp_path / "projects", style=_NORMALIZED_STYLE)
    advance_project_schema(project_dir, to_version=13)
    project_before = _read_project(project_dir)
    before = dict(_entries(project_dir))

    advance_project_schema(project_dir, to_version=14)

    assert _read_project(project_dir) == {**project_before, "schema_version": 14}
    assert _entries(project_dir) == before


def test_storyboard_basis_digest_survives_the_cleanup(tmp_path: Path) -> None:
    project_dir = _legacy_project_at_v13(tmp_path / "projects")
    before = _entries(project_dir)[_STORYBOARD]

    migrate_project_dir(project_dir)

    assert _entries(project_dir)[_STORYBOARD] == before
    assert _status(project_dir, _STORYBOARD) == "current"


def test_asset_sheets_and_grids_stay_current_after_the_cleanup(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = _legacy_project_at_v13(root)
    for key in (_CHARACTER_SHEET, _SCENE_SHEET, _GRID, _GRID_MEMBER):
        assert _is_current_before_migration(project_dir, key)

    migrate_project_dir(project_dir)

    for key in (_CHARACTER_SHEET, _SCENE_SHEET, _GRID, _GRID_MEMBER):
        assert _status(project_dir, key) == "current"
    summary = WorkflowStateService(ProjectManager(root)).get_project_summary(project_dir.name)
    assert (summary.assets["character"].available, summary.assets["character"].stale) == (1, 0)
    assert (summary.assets["scene"].available, summary.assets["scene"].stale) == (1, 0)
    assert (summary.episodes[0].storyboards.available, summary.episodes[0].storyboards.stale) == (3, 0)


def test_asset_sheet_basis_digest_is_rebased_onto_the_normalized_style(tmp_path: Path) -> None:
    """资产图依据记的是原始风格值，归一必然改摘要——保住时新性靠改写登记，不靠摘要不变。"""

    project_dir = _legacy_project_at_v13(tmp_path / "projects")
    before = _entries(project_dir)[_CHARACTER_SHEET]

    migrate_project_dir(project_dir)

    after = _entries(project_dir)[_CHARACTER_SHEET]
    assert after.basis_digest != before.basis_digest
    assert after.artifact_path == before.artifact_path


def test_already_stale_artifact_is_not_made_current(tmp_path: Path) -> None:
    project_dir = _legacy_project_at_v13(tmp_path / "projects")
    project = _read_project(project_dir)
    project["scenes"]["雨巷"]["description"] = "改过的场景描述"
    _write_project(project_dir, project)
    assert not _is_current_before_migration(project_dir, _SCENE_SHEET)
    before = _entries(project_dir)[_SCENE_SHEET]

    migrate_project_dir(project_dir)

    assert _entries(project_dir)[_SCENE_SHEET] == before
    assert _status(project_dir, _SCENE_SHEET) == "stale"
    assert _status(project_dir, _CHARACTER_SHEET) == "current"


def test_non_style_dependency_change_between_plans_aborts_without_rebasing(tmp_path: Path) -> None:
    project_dir = _legacy_project_at_v13(tmp_path / "projects")
    before = _entries(project_dir)[_SCRIPT]
    original_plan = TargetStatePlanner.plan
    calls = 0

    def plan_with_dependency_change(planner: TargetStatePlanner):
        nonlocal calls
        result = original_plan(planner)
        calls += 1
        if calls == 1:
            (project_dir / "drafts" / "episode_1" / "script_plan_segments.json").write_text(
                json.dumps({"segments": [{"novel_text": "非风格输入已改变"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
        return result

    with (
        patch.object(TargetStatePlanner, "plan", plan_with_dependency_change),
        pytest.raises(RuntimeError, match="dependency changed after preflight"),
    ):
        migrate_project_dir(project_dir)

    assert _entries(project_dir)[_SCRIPT] == before
    assert _read_project(project_dir)["schema_version"] == 13
