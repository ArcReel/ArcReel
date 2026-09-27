"""分镜图引用的资产图在升级时不能登记：该分镜图不登记、进迁移报告，经整条迁移链后的制作状态。"""

from __future__ import annotations

import json
from pathlib import Path

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import load_migration_report
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.source_revision import SourceScope, compute_source_revision
from lib.workflow.workflow_state import WorkflowStateService
from tests.legacy_project_shapes import write_legacy_storyboard_project


def _legacy_project(root: Path) -> Path:
    project_dir = write_legacy_storyboard_project(
        root, "legacy-storyboard-unregistrable-reference", unit_ids=("E1S1", "E1S2", "E1S3")
    )
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["characters"] = {
        "张三": {"description": "主角", "character_sheet": "characters/张三.png"},
        "李四": {"description": "配角", "character_sheet": "characters/李四.png"},
    }
    (project_dir / "characters").mkdir(exist_ok=True)
    (project_dir / "characters" / "张三.png").write_bytes(b"sheet-zhangsan")
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    for segment, character in zip(script["segments"], ("张三", "李四", "张三"), strict=True):
        segment["characters_in_segment"] = [character]
    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    revision = compute_source_revision(project_dir, project, SourceScope(kind="all")).revision
    project["workflow"] = {"asset_inventory": {"scope": {"kind": "all", "files": []}, "source_revision": revision}}
    project_path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    return project_dir


def _status(project_dir: Path, unit_id: str) -> str:
    comparison = ArtifactCurrencyResolver(project_dir).compare(
        ArtifactKey.episode_storyboard(1, unit_id), artifact_path=f"storyboards/scene_{unit_id}.png"
    )
    return comparison.status.value


def test_storyboard_with_an_unregistrable_sheet_is_reported_and_reads_missing(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = _legacy_project(root)

    assert migrate_project_dir(project_dir) is True

    report = load_migration_report(project_dir)
    assert report is not None
    [skipped] = [item for item in report.skipped if item.kind == "episode-storyboard"]
    assert (skipped.resource_id, skipped.artifact_path) == ("E1S2", "storyboards/scene_E1S2.png")
    assert "reference_asset_missing" in skipped.reason
    # 上一分镜图未登记只是略去：E1S3 按不带它的依据登记，读为最新。
    assert [_status(project_dir, unit_id) for unit_id in ("E1S1", "E1S2", "E1S3")] == ["current", "missing", "current"]
    storyboards = (
        WorkflowStateService(ProjectManager(tmp_path)).get_project_summary(project_dir.name).episodes[0].storyboards
    )
    assert (storyboards.available, storyboards.stale) == (2, 0)
