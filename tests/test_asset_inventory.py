from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.asset_inventory import AssetInventoryRevisionConflict, complete_asset_inventory
from lib.project_manager import ProjectManager
from lib.source_revision import SourceScope, compute_source_revision
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.asset_inventory import complete_asset_inventory_tool


def _make_project(tmp_path: Path) -> tuple[ProjectManager, Path]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "", "narration")
    project_path = pm.get_project_path("demo")
    (project_path / "source" / "novel.txt").write_text("最初的原文", encoding="utf-8")
    return pm, project_path


@pytest.mark.integration
def test_complete_inventory_accepts_three_empty_buckets_and_persists_scope(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    project = pm.load_project("demo")
    expected = compute_source_revision(project_path, project, SourceScope(kind="all")).revision
    assert expected is not None

    completed = complete_asset_inventory(pm, "demo", SourceScope(kind="all"), expected)

    assert completed.counts == {"characters": 0, "scenes": 0, "props": 0}
    marker = pm.load_project("demo")["workflow"]["asset_inventory"]
    assert marker["scope"] == {"kind": "all", "files": []}
    assert marker["source_revision"] == expected
    assert marker["completed_at"].endswith("+00:00")


@pytest.mark.integration
def test_revision_conflict_does_not_partially_write_inventory_marker(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    project = pm.load_project("demo")
    stale = compute_source_revision(project_path, project, SourceScope(kind="all")).revision
    assert stale is not None
    (project_path / "source" / "novel.txt").write_text("修改后的原文", encoding="utf-8")

    with pytest.raises(AssetInventoryRevisionConflict) as raised:
        complete_asset_inventory(pm, "demo", SourceScope(kind="all"), stale)

    assert raised.value.actual_revision != stale
    assert "workflow" not in pm.load_project("demo")


@pytest.mark.integration
def test_scoped_completion_keeps_explicit_partial_scope(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    project = pm.load_project("demo")
    scope = SourceScope(kind="files", files=["source/novel.txt"])
    expected = compute_source_revision(project_path, project, scope).revision
    assert expected is not None

    complete_asset_inventory(pm, "demo", scope, expected)

    marker = pm.load_project("demo")["workflow"]["asset_inventory"]
    assert marker["scope"] == {"kind": "files", "files": ["source/novel.txt"]}


@pytest.mark.integration
async def test_complete_inventory_mcp_returns_machine_readable_result_and_conflict(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    tool = complete_asset_inventory_tool(ctx)
    expected = compute_source_revision(project_path, pm.load_project("demo"), SourceScope(kind="all")).revision
    assert expected is not None

    success = await tool.handler({"scope": {"kind": "all", "files": []}, "expected_source_revision": expected})
    body = json.loads(success["content"][0]["text"])
    assert body == {
        "counts": {"characters": 0, "props": 0, "scenes": 0},
        "scope": {"files": [], "kind": "all"},
        "source_revision": expected,
    }

    (project_path / "source" / "novel.txt").write_text("又一次变化", encoding="utf-8")
    conflict = await tool.handler({"scope": {"kind": "all", "files": []}, "expected_source_revision": expected})
    conflict_body = json.loads(conflict["content"][0]["text"])
    assert conflict["is_error"] is True
    assert conflict_body["error"] == "source_revision_conflict"
    assert conflict_body["expected_source_revision"] == expected
    assert conflict_body["actual_source_revision"] != expected
