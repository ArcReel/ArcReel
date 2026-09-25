"""待补全工具（complete_asset_inventory / complete_script_plan_rebuild）经声明入口的 ``ToolOutcome``。

提交完成事实的服务函数与线程卸载经 handler 的关键字参数注入替身；请求校验走两宿主共用的声明入口。
"""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.project.asset_inventory import complete_asset_inventory
from lib.project.project_manager import ProjectManager
from lib.project.source_revision import SourceScope, compute_source_revision
from server.agent_toolset.declaration import ToolDeclaration, invoke_declaration
from server.agent_toolset.workflow_completion import COMPLETE_ASSET_INVENTORY, COMPLETE_SCRIPT_PLAN_REBUILD
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import (
    CallerContext,
    CompleteAssetInventoryResult,
    CompleteScriptPlanRebuildResult,
    ProjectScope,
    Services,
    ToolOutcome,
)


def _project(tmp_path: Path) -> ProjectManager:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo")
    projects.create_project_metadata("demo", "Demo", "", "narration")
    (projects.get_project_path("demo") / "source" / "novel.txt").write_text("最初的原文", encoding="utf-8")
    return projects


async def _run(
    declaration: ToolDeclaration[Any, Any], projects: ProjectManager, arguments: dict[str, Any], **collaborators: Any
) -> ToolOutcome[Any]:
    """经声明入口调用，领域协作者经 handler 关键字参数注入。"""
    injected = replace(declaration, handler=partial(declaration.handler, **collaborators))
    services = Services(
        projects=projects,
        workflow_planner=WorkflowPlanner(projects),
        capabilities=ConfigResolver(async_session_factory),
    )
    return await invoke_declaration(
        injected,
        arguments,
        ProjectScope(project_name="demo", data_root=projects.data_root),
        CallerContext(user_id="u1", source="embedded"),
        services,
    )


def _source_revision(projects: ProjectManager) -> str:
    revision = compute_source_revision(
        projects.get_project_path("demo"), projects.load_project("demo"), SourceScope(kind="all")
    ).revision
    assert revision is not None
    return revision


# ---------------------------------------------------------------------------
# complete_asset_inventory
# ---------------------------------------------------------------------------


async def test_asset_inventory_commits_off_the_event_loop_then_refuses_a_changed_source(tmp_path: Path) -> None:
    projects = _project(tmp_path)
    offloads: list[object] = []

    async def run_sync(fn: Any, *args: Any) -> Any:
        offloads.append(fn)
        return fn(*args)

    expected = _source_revision(projects)
    arguments = {"scope": {"kind": "all", "files": []}, "expected_source_revision": expected}

    success = await _run(COMPLETE_ASSET_INVENTORY, projects, arguments, run_sync=run_sync)

    assert success.problem is None
    assert success.value == CompleteAssetInventoryResult(
        scope=SourceScope(kind="all"),
        source_revision=expected,
        counts={"characters": 0, "scenes": 0, "props": 0},
    )

    (projects.get_project_path("demo") / "source" / "novel.txt").write_text("又一次变化", encoding="utf-8")
    conflict = await _run(COMPLETE_ASSET_INVENTORY, projects, arguments, run_sync=run_sync)

    assert conflict.problem is not None
    assert conflict.problem.code == "source_revision_conflict"
    assert conflict.problem.params is not None
    assert conflict.problem.params["expected_source_revision"] == expected
    assert conflict.problem.params["actual_source_revision"] != expected
    assert offloads == [complete_asset_inventory, complete_asset_inventory]


async def test_asset_inventory_distinguishes_a_bad_revision_from_a_broken_workflow(tmp_path: Path) -> None:
    projects = _project(tmp_path)

    invalid = await _run(
        COMPLETE_ASSET_INVENTORY,
        projects,
        {"scope": {"kind": "all", "files": []}, "expected_source_revision": "not-a-revision"},
    )
    assert invalid.problem is not None
    assert invalid.problem.code == "invalid_request"

    expected = _source_revision(projects)
    projects.update_project("demo", lambda project: project.update(workflow="broken"))
    unavailable = await _run(
        COMPLETE_ASSET_INVENTORY,
        projects,
        {"scope": {"kind": "all", "files": []}, "expected_source_revision": expected},
    )
    assert unavailable.problem is not None
    assert unavailable.problem.code == "inventory_unavailable"


# ---------------------------------------------------------------------------
# complete_script_plan_rebuild
# ---------------------------------------------------------------------------


async def test_script_plan_rebuild_forwards_the_explicit_baseline(tmp_path: Path) -> None:
    projects = _project(tmp_path)
    calls: list[tuple[object, ...]] = []

    def complete(*args: object) -> str:
        calls.append(args)
        return "rebuilt-revision"

    outcome = await _run(
        COMPLETE_SCRIPT_PLAN_REBUILD,
        projects,
        {"episode": 2, "expected_stale_script_plan_revision": "baseline"},
        complete=complete,
    )

    assert outcome.problem is None
    assert outcome.value == CompleteScriptPlanRebuildResult(episode=2, script_plan_revision="rebuilt-revision")
    assert calls == [(projects, "demo", 2, "baseline")]


async def test_script_plan_rebuild_requires_the_baseline_to_be_passed_explicitly(tmp_path: Path) -> None:
    projects = _project(tmp_path)
    calls: list[tuple[object, ...]] = []

    def complete(*args: object) -> str:
        calls.append(args)
        return "rebuilt-revision"

    outcome = await _run(COMPLETE_SCRIPT_PLAN_REBUILD, projects, {"episode": 1}, complete=complete)

    assert outcome.problem is not None
    assert outcome.problem.code == "invalid_request"
    assert outcome.problem.params is not None
    assert outcome.problem.params["errors"][0]["loc"] == ["expected_stale_script_plan_revision"]
    assert calls == []
