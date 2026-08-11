from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.asset_inventory import AssetInventoryInvalidRequest, AssetInventoryRevisionConflict, complete_asset_inventory
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
def test_revision_conflict_does_not_partially_write_extracted_assets(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    project = pm.load_project("demo")
    stale = compute_source_revision(project_path, project, SourceScope(kind="all")).revision
    assert stale is not None
    (project_path / "source" / "novel.txt").write_text("修改后的原文", encoding="utf-8")

    with pytest.raises(AssetInventoryRevisionConflict):
        complete_asset_inventory(
            pm,
            "demo",
            SourceScope(kind="all"),
            stale,
            {"characters": {"阿青": {"description": "青衣少女", "voice_style": "清亮"}}},
        )

    saved = pm.load_project("demo")
    assert "阿青" not in saved["characters"]
    assert "workflow" not in saved


@pytest.mark.integration
def test_extracted_assets_and_marker_commit_together(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    expected = compute_source_revision(project_path, pm.load_project("demo"), SourceScope(kind="all")).revision
    assert expected is not None

    completed = complete_asset_inventory(
        pm,
        "demo",
        SourceScope(kind="all"),
        expected,
        {
            "characters": {"阿青": {"description": "青衣少女", "voice_style": "清亮"}},
            "scenes": {"竹林": {"description": "雨后竹林"}},
            "props": {},
        },
    )

    saved = pm.load_project("demo")
    assert saved["characters"]["阿青"]["voice_style"] == "清亮"
    assert saved["scenes"]["竹林"]["description"] == "雨后竹林"
    assert saved["workflow"]["asset_inventory"]["source_revision"] == expected
    assert completed.counts == {"characters": 1, "scenes": 1, "props": 0}


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
def test_complete_inventory_rejects_non_string_expected_revision(tmp_path: Path) -> None:
    pm, _project_path = _make_project(tmp_path)

    with pytest.raises(AssetInventoryInvalidRequest):
        complete_asset_inventory(pm, "demo", SourceScope(kind="all"), None)

    with pytest.raises(AssetInventoryInvalidRequest):
        complete_asset_inventory(pm, "demo", SourceScope(kind="all"), "sha256-v1:not-a-digest")


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


@pytest.mark.integration
async def test_complete_inventory_mcp_distinguishes_invalid_request_from_broken_workflow(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    tool = complete_asset_inventory_tool(ctx)

    invalid = await tool.handler({"scope": {"kind": "all", "files": []}, "expected_source_revision": "not-a-revision"})
    assert json.loads(invalid["content"][0]["text"])["error"] == "invalid_request"

    expected = compute_source_revision(
        project_path,
        pm.load_project("demo"),
        SourceScope(kind="all"),
    ).revision
    assert expected is not None
    pm.update_project("demo", lambda project: project.update(workflow="broken"))

    unavailable = await tool.handler({"scope": {"kind": "all", "files": []}, "expected_source_revision": expected})
    assert json.loads(unavailable["content"][0]["text"])["error"] == "inventory_unavailable"
