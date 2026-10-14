"""分镜图引用的资产图在升级时不能登记：该分镜图照常补录、读作 current，经整条迁移链后的制作状态。

生成缺口只拦生成（ADR 0098）：v17→v18 补录此前因缺口没登记的分镜图，已登记条目不动。
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.artifacts.artifact_registration import register_current_artifact
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


def test_storyboard_with_an_unregistrable_sheet_is_backfilled_and_reads_current(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = _legacy_project(root)

    assert migrate_project_dir(project_dir) is True

    report = load_migration_report(project_dir)
    assert report is not None
    assert [item for item in report.skipped if item.kind == "episode-storyboard"] == []
    # E1S3 早先按不带上一分镜图的依据登记，v17→v18 不动已登记条目；E1S2 补录后成为它可用的上一分镜图，
    # 期望依据随之带上 E1S2，E1S3 读为过期——与运行期上一分镜图变为可用时同一口径。
    assert [_status(project_dir, unit_id) for unit_id in ("E1S1", "E1S2", "E1S3")] == ["current", "current", "stale"]
    storyboards = (
        WorkflowStateService(ProjectManager(tmp_path)).get_project_summary(project_dir.name).episodes[0].storyboards
    )
    assert (storyboards.available, storyboards.stale) == (3, 1)


def test_storyboard_referencing_a_prop_without_a_sheet_reads_stale_once_the_sheet_exists(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = write_legacy_storyboard_project(root, "legacy-storyboard-prop-without-sheet", unit_ids=("E1S1",))
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["props"] = {"玉佩": {"description": "一枚玉佩"}}
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["segments"][0]["props"] = ["玉佩"]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    migrate_project_dir(project_dir)

    assert _status(project_dir, "E1S1") == "current"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["props"]["玉佩"]["prop_sheet"] = "props/玉佩.png"
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (project_dir / "props").mkdir()
    (project_dir / "props" / "玉佩.png").write_bytes(b"sheet-prop")
    register_current_artifact(project_dir, ArtifactKey.asset_sheet("prop", "玉佩"))

    assert _status(project_dir, "E1S1") == "stale"
