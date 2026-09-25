"""资产图与衍生资产图的生成输入在升级时不成立：不登记、进迁移报告，经整条迁移链后的制作状态。

旧项目里可能有：声明了原图却读不到的角色、描述被清空的场景，以及本体资产图登记不了的衍生。
旧规划器对前两者静默不登记、不进报告，对衍生只看本体资产图文件在不在。
"""

from __future__ import annotations

from pathlib import Path

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import load_migration_report
from lib.project.project_migrations.runner import migrate_project_dir
from lib.workflow.workflow_state import WorkflowStateService
from tests.legacy_project_shapes import write_legacy_unregistrable_asset_sheet_project

_SHEETS = {
    ("character", "张三"): "characters/张三.png",
    ("character", "张三/劲装"): "characters/derivatives/张三/劲装.png",
    ("character", "李四"): "characters/李四.png",
    ("character", "李四/便装"): "characters/derivatives/李四/便装.png",
    ("scene", "祠堂"): "scenes/祠堂.png",
}


def _status(project_dir: Path, asset_type: str, name: str) -> str:
    comparison = ArtifactCurrencyResolver(project_dir).compare(
        ArtifactKey.asset_sheet(asset_type, name), artifact_path=_SHEETS[(asset_type, name)]
    )
    return comparison.status.value


def test_asset_sheets_without_a_valid_generation_input_are_reported_and_read_missing(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = write_legacy_unregistrable_asset_sheet_project(root)

    assert migrate_project_dir(project_dir) is True

    report = load_migration_report(project_dir)
    assert report is not None
    reasons = {item.resource_id: item.reason for item in report.skipped if item.kind == "asset-sheet"}
    assert set(reasons) == {"张三", "张三/劲装", "祠堂"}
    assert "asset_original_missing" in reasons["张三"]
    assert "derivative_owner_sheet_missing" in reasons["张三/劲装"]
    assert "asset_description_required" in reasons["祠堂"]
    assert {key: _status(project_dir, *key) for key in _SHEETS} == {
        ("character", "张三"): "missing",
        ("character", "张三/劲装"): "missing",
        ("character", "李四"): "current",
        ("character", "李四/便装"): "current",
        ("scene", "祠堂"): "missing",
    }
    status = WorkflowStateService(ProjectManager(tmp_path)).get_status(project_dir.name)
    characters = status.artifacts["asset_sheets"]["character"]
    assert characters["current_ids"] == ["李四"]
    assert characters["missing_ids"] == ["张三"]
