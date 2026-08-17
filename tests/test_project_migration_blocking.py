"""迁移失败 → 项目级阻断 → 修复 → 重试成功的完整路径。

断言的是外部可观察的输出：磁盘上的裁决记录、制作状态的 blocker、制作计划的单条 problem、
入队被拒、以及重试工具的返回，不断言内部调用顺序。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from claude_agent_sdk import tool

from lib.api_errors import ConflictError
from lib.generation_result import GenerationAction, GenerationProblemCode
from lib.project_manager import ProjectManager
from lib.project_migration_failure import (
    MIGRATION_FAILURE_CODE,
    MIGRATION_FAILURE_FILENAME,
    RETRY_MIGRATION_ACTION,
    load_migration_failure,
)
from lib.project_migrations.runner import migrate_project_with_verdict, run_project_migrations
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.workflow_plan import WorkflowPlanRequest
from lib.workflow_state import WorkflowStateService
from server.services.workflow_planner import WorkflowPlanner
from tests.test_project_migration_v7_v8 import _project

pytestmark = pytest.mark.integration


def _break_episode_script(project_dir: Path) -> None:
    """Drop the identity off one item — a violation the activation preflight names."""

    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    del script["segments"][0]["segment_id"]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def _repair_episode_script(project_dir: Path) -> None:
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["segments"][0]["segment_id"] = "E1S01"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def test_failed_migration_records_the_offending_episode_and_file(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    _break_episode_script(project_dir)

    failure = migrate_project_with_verdict(project_dir)

    assert failure is not None
    assert failure.schema_version == CURRENT_PROJECT_SCHEMA_VERSION - 1
    assert failure.reason
    assert [(d.episode, d.file) for d in failure.details] == [(1, "scripts/episode_1.json")]
    assert "identity" in failure.details[0].violation
    assert (project_dir / MIGRATION_FAILURE_FILENAME).exists()


def test_startup_run_records_the_verdict_and_clears_it_once_repaired(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)

    summary = run_project_migrations(projects_root)
    assert summary.failed == ["demo"]
    assert load_migration_failure(project_dir) is not None

    _repair_episode_script(project_dir)
    summary = run_project_migrations(projects_root)

    assert summary.migrated == ["demo"]
    assert load_migration_failure(project_dir) is None
    assert not (project_dir / MIGRATION_FAILURE_FILENAME).exists()


def test_workflow_status_reports_exactly_one_blocker_with_the_raw_reason(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)
    failure = migrate_project_with_verdict(project_dir)
    assert failure is not None

    status = WorkflowStateService(ProjectManager(str(projects_root))).get_status("demo")

    assert [blocker.code for blocker in status.blockers] == [MIGRATION_FAILURE_CODE]
    assert status.blockers[0].reason == failure.reason
    assert status.next_action.type == RETRY_MIGRATION_ACTION


async def test_workflow_plan_reports_exactly_one_problem_pointing_at_the_retry(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)
    failure = migrate_project_with_verdict(project_dir)
    assert failure is not None

    plan = await WorkflowPlanner(ProjectManager(str(projects_root))).get_plan("demo", WorkflowPlanRequest())

    assert len(plan.problems) == 1
    problem = plan.problems[0]
    assert problem.code == GenerationProblemCode.PROJECT_MIGRATION_FAILED
    assert problem.detail == failure.reason
    assert problem.action == GenerationAction.RETRY_PROJECT_MIGRATION
    assert problem.params["details"][0]["episode"] == 1
    assert plan.next_action.type == RETRY_MIGRATION_ACTION


def test_project_status_marks_the_project_for_repair(tmp_path: Path) -> None:
    from lib.status_calculator import StatusCalculator

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)
    failure = migrate_project_with_verdict(project_dir)
    assert failure is not None

    pm = ProjectManager(str(projects_root))
    status = StatusCalculator(pm).calculate_project_status("demo", pm.load_project_readonly("demo"))

    assert status["needs_repair"] is True
    assert status["repair_reason"] == failure.reason


def test_generation_entries_refuse_while_the_project_is_blocked(tmp_path: Path, monkeypatch) -> None:
    import lib.project_migration_guard as guard

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)
    assert migrate_project_with_verdict(project_dir) is not None

    pm = ProjectManager(str(projects_root))
    monkeypatch.setattr(guard, "get_project_manager", lambda: pm)

    with pytest.raises(ConflictError) as excinfo:
        guard.assert_project_migration_ok("demo")
    assert excinfo.value.key == MIGRATION_FAILURE_CODE

    _repair_episode_script(project_dir)
    assert migrate_project_with_verdict(project_dir) is None
    guard.assert_project_migration_ok("demo")


async def test_retry_tool_returns_details_then_unblocks_once_repaired(tmp_path: Path) -> None:
    from server.agent_runtime.sdk_tools._context import ToolContext
    from server.agent_runtime.sdk_tools.retry_project_migration import retry_project_migration_tool

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)
    assert migrate_project_with_verdict(project_dir) is not None

    ctx = ToolContext(project_name="demo", projects_root=projects_root, pm=ProjectManager(str(projects_root)))
    handler = retry_project_migration_tool(ctx).handler

    blocked = await handler({})
    assert blocked["is_error"] is True
    payload = json.loads(blocked["content"][0]["text"].split("\n", 1)[1])
    assert payload["error"] == MIGRATION_FAILURE_CODE
    assert payload["details"][0]["episode"] == 1
    assert payload["details"][0]["file"] == "scripts/episode_1.json"

    _repair_episode_script(project_dir)
    unblocked = await handler({})

    assert unblocked.get("is_error") is not True
    assert load_migration_failure(project_dir) is None
    assert unblocked["workflow_plan"]["status"]["blockers"] == []


async def test_mcp_generation_tools_report_the_same_problem_without_running(tmp_path: Path, monkeypatch) -> None:
    import lib.project_migration_guard as guard
    from server.agent_runtime import sdk_tools

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)
    failure = migrate_project_with_verdict(project_dir)
    assert failure is not None

    pm = ProjectManager(str(projects_root))
    monkeypatch.setattr(guard, "get_project_manager", lambda: pm)
    ctx = sdk_tools.ToolContext(project_name="demo", projects_root=projects_root, pm=pm)
    ran = False

    @tool("generate_storyboards", "stub", {"type": "object", "properties": {}})
    async def _inner(_args: dict[str, object]) -> dict[str, object]:
        nonlocal ran
        ran = True
        return {"content": []}

    guarded = sdk_tools._refuse_while_migration_failed(_inner, ctx)  # pyright: ignore[reportPrivateUsage]
    blocked = await guarded.handler({"segment_ids": ["E1S01"]})

    assert ran is False
    assert blocked["is_error"] is True
    assert blocked["problem"]["code"] == GenerationProblemCode.PROJECT_MIGRATION_FAILED
    assert blocked["problem"]["action"] == GenerationAction.RETRY_PROJECT_MIGRATION
    assert blocked["problem"]["detail"] == failure.reason
    # The blocked set names real tools, and never the retry tool — it is the way out.
    assert sdk_tools.MIGRATION_BLOCKED_TOOL_IDS <= set(sdk_tools.ARCREEL_MCP_TOOL_IDS)
    assert "retry_project_migration" not in sdk_tools.MIGRATION_BLOCKED_TOOL_IDS


def test_a_repaired_project_is_idempotent_to_retry(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)

    assert migrate_project_with_verdict(project_dir) is None
    # Already at the current schema: rerunning is a no-op success, not a second migration.
    assert migrate_project_with_verdict(project_dir) is None
    assert load_migration_failure(project_dir) is None
