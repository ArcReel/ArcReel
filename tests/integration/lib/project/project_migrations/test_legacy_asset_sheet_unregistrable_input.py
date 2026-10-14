"""资产图与衍生资产图的生成输入在升级时有缺口：照常登记、读作 current，经整条迁移链后的制作状态。

旧项目里可能有：声明了原图却读不到的角色、描述被清空的场景，以及本体有缺口的衍生。生成缺口只拦
生成（ADR 0098）：v17→v18 把此前因缺口没登记的资产图补录，之后补齐缺口按常规判过期。
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.artifacts.generation_input import asset_sheet_input, project_input_observation
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


def test_asset_sheets_with_generation_input_gaps_are_backfilled_and_read_current(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = write_legacy_unregistrable_asset_sheet_project(root)

    assert migrate_project_dir(project_dir) is True

    report = load_migration_report(project_dir)
    assert report is not None
    assert [item for item in report.skipped if item.kind == "asset-sheet"] == []
    assert {key: _status(project_dir, *key) for key in _SHEETS} == dict.fromkeys(_SHEETS, "current")
    status = WorkflowStateService(ProjectManager(tmp_path)).get_status(project_dir.name)
    characters = status.artifacts["asset_sheets"]["character"]
    assert characters["current_ids"] == ["张三", "李四"]
    assert characters["missing_ids"] == []
    # 生成仍被拒：缺口只拦生成。
    project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    refusal = asset_sheet_input(
        project, asset_type="scene", name="祠堂", observation=project_input_observation(project_dir)
    ).refusal
    assert refusal is not None
    assert refusal.codes == ("asset_description_required",)


def test_filling_in_the_description_afterwards_reads_the_sheet_stale(tmp_path: Path) -> None:
    project_dir = write_legacy_unregistrable_asset_sheet_project(tmp_path / "projects")
    migrate_project_dir(project_dir)
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["scenes"]["祠堂"]["description"] = "古旧祠堂"
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    assert _status(project_dir, "scene", "祠堂") == "stale"
    assert _status(project_dir, "character", "李四") == "current"
