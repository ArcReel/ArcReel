"""待补全工具（complete_script_plan_rebuild）经声明入口的 ``ToolOutcome``。

提交完成事实的服务函数与线程卸载经 handler 的关键字参数注入替身；请求校验走两宿主共用的声明入口。
"""

from __future__ import annotations

from pathlib import Path

from lib.project.project_manager import ProjectManager
from server.agent_toolset.workflow_completion import COMPLETE_SCRIPT_PLAN_REBUILD
from server.tool_runtime import CompleteScriptPlanRebuildResult
from tests.factories import register_project_sources
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool


def _project(tmp_path: Path) -> ProjectManager:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo")
    projects.create_project_metadata("demo", "Demo", "", "narration")
    register_project_sources(projects, "demo", whole_source={"novel.txt": "最初的原文"})
    return projects


def _harness(projects: ProjectManager) -> ToolHarness:
    return ToolHarness(project_name="demo", data_root=projects.data_root, pm=projects)


# ---------------------------------------------------------------------------
# complete_script_plan_rebuild
# ---------------------------------------------------------------------------


async def test_script_plan_rebuild_forwards_the_explicit_baseline(tmp_path: Path) -> None:
    projects = _project(tmp_path)
    calls: list[tuple[object, ...]] = []

    def complete(*args: object) -> str:
        calls.append(args)
        return "rebuilt-revision"

    outcome = await run_declared_tool(
        COMPLETE_SCRIPT_PLAN_REBUILD,
        _harness(projects),
        {"episode_id": 2, "expected_stale_script_plan_revision": "baseline"},
        complete=complete,
    )

    assert outcome.problem is None
    assert outcome.value == CompleteScriptPlanRebuildResult(episode_id=2, script_plan_revision="rebuilt-revision")
    assert calls == [(projects, "demo", 2, "baseline")]


async def test_script_plan_rebuild_requires_the_baseline_to_be_passed_explicitly(tmp_path: Path) -> None:
    projects = _project(tmp_path)
    calls: list[tuple[object, ...]] = []

    def complete(*args: object) -> str:
        calls.append(args)
        return "rebuilt-revision"

    outcome = await run_declared_tool(
        COMPLETE_SCRIPT_PLAN_REBUILD, _harness(projects), {"episode_id": 1}, complete=complete
    )

    assert outcome.problem is not None
    assert outcome.problem.code == "invalid_request"
    assert outcome.problem.params is not None
    assert outcome.problem.params["errors"][0]["loc"] == ["expected_stale_script_plan_revision"]
    assert calls == []
