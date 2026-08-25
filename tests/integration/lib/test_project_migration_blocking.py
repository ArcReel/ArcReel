"""迁移失败 → 项目级阻断 → 修复 → 重试成功的完整路径。

断言的是外部可观察的输出：磁盘上的裁决记录、制作状态的 blocker、制作计划的单条 problem、
入队被拒、以及重试工具的返回，不断言内部调用顺序。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from claude_agent_sdk import tool
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from lib.api_errors import ConflictError
from lib.artifact_activation import ARTIFACT_MANIFEST_SCHEMA_VERSION
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
from lib.script_batch_edit import script_revision
from lib.workflow_plan import WorkflowPlanRequest
from lib.workflow_state import WorkflowStateService
from server.agent_runtime.sdk_tools.content_read import get_episode_script_tool
from server.agent_runtime.sdk_tools.enqueue_assets import list_pending_assets_tool
from server.agent_runtime.sdk_tools.patch_script import patch_episode_script_tool
from server.dependencies import require_project_migration_ok
from server.error_handlers import register_error_handlers
from server.media_tools.context import ToolContext
from server.services.workflow_planner import WorkflowPlanner
from tests.integration.lib.project_migrations.test_project_migration_v7_v8 import _project


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
    # 项目卡在清单激活这一步的起点版本上，不是迁移链的末端。
    assert failure.schema_version == ARTIFACT_MANIFEST_SCHEMA_VERSION - 1
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
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)
    failure = migrate_project_with_verdict(project_dir)
    assert failure is not None

    summary = WorkflowStateService(ProjectManager(str(projects_root))).get_project_summary("demo")

    assert summary.needs_repair is True
    assert summary.repair_reason == failure.reason


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
    from server.agent_runtime.sdk_tools.retry_project_migration import retry_project_migration_tool
    from server.media_tools.context import ToolContext

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)
    assert migrate_project_with_verdict(project_dir) is not None

    ctx = ToolContext(project_name="demo", projects_root=projects_root, pm=ProjectManager(str(projects_root)))
    handler = retry_project_migration_tool(ctx).handler

    blocked = await handler({})
    assert blocked["is_error"] is True
    # 与被拦截的生成工具同一个回执形状：一份 problem，不是第二套 error/reason 信封
    assert blocked["problem"]["code"] == MIGRATION_FAILURE_CODE
    assert blocked["problem"]["params"]["details"][0]["episode"] == 1
    assert blocked["problem"]["params"]["details"][0]["file"] == "scripts/episode_1.json"
    assert json.loads(blocked["content"][0]["text"])["problem"] == blocked["problem"]

    _repair_episode_script(project_dir)
    unblocked = await handler({})

    assert unblocked.get("is_error") is not True
    assert load_migration_failure(project_dir) is None
    assert json.loads(unblocked["content"][0]["text"])["migration_retry"]["workflow_plan"]["status"]["blockers"] == []


def _assert_list_pending_assets_unblocked(unblocked: dict, ctx: ToolContext) -> None:
    text = unblocked["content"][0]["text"]
    assert ctx.project_name in text
    assert "✅" in text


def _assert_get_episode_script_unblocked(unblocked: dict, ctx: ToolContext) -> None:
    payload = json.loads(unblocked["content"][0]["text"])["episode_script"]
    assert payload["script_filename"] == "episode_1.json"
    assert payload["revision"] == script_revision(ctx.pm.load_script_readonly(ctx.project_name, "episode_1.json"))


@pytest.mark.parametrize(
    "tool_factory,args,unblocked_fields,assert_unblocked",
    [
        (list_pending_assets_tool, {}, (), _assert_list_pending_assets_unblocked),
        (
            get_episode_script_tool,
            {"script": "episode_1.json"},
            (),
            _assert_get_episode_script_unblocked,
        ),
    ],
)
async def test_readonly_diagnostic_tools_report_the_migration_problem_instead_of_raising(
    tmp_path: Path, tool_factory, args, unblocked_fields, assert_unblocked
) -> None:
    """只读诊断工具不在 MIGRATION_BLOCKED_TOOL_IDS 里、不经注册期守卫包装，各自在 handler 内

    读一次迁移裁决：命中则返回与生成类工具同构的 problem 回执，裁决清空后照常给出结果。
    """

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)
    failure = migrate_project_with_verdict(project_dir)
    assert failure is not None

    ctx = ToolContext(project_name="demo", projects_root=projects_root, pm=ProjectManager(str(projects_root)))
    handler = tool_factory(ctx).handler

    blocked = await handler(args)

    assert blocked["is_error"] is True
    assert blocked["problem"]["code"] == MIGRATION_FAILURE_CODE
    assert blocked["problem"]["action"] == RETRY_MIGRATION_ACTION
    assert blocked["problem"]["detail"] == failure.reason
    assert failure.reason in blocked["content"][0]["text"]

    _repair_episode_script(project_dir)
    assert migrate_project_with_verdict(project_dir) is None
    unblocked = await handler(args)

    assert unblocked.get("is_error") is not True
    assert "problem" not in unblocked
    for field in unblocked_fields:
        assert unblocked[field]
    assert_unblocked(unblocked, ctx)


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


@pytest.mark.parametrize(
    "tool_factory",
    [patch_episode_script_tool],
)
async def test_script_edit_mcp_tools_refuse_at_registration_on_a_migration_blocked_project(
    tmp_path: Path, monkeypatch, tool_factory
) -> None:
    """受控剧本编辑工具登记在 MIGRATION_BLOCKED_TOOL_IDS 里，注册期包上守卫后直接拒。

    断言两件事：工具 id 在阻断集内（``build_arcreel_mcp_server`` 就按这个集合决定包不包守卫），
    以及包上后的回执是 problem 形状。不落到 ScriptBatchEditor.execute 的内层裁决——那条仍在，
    作兜底，但命中它会返回 ``script_edit`` 信封而不是 ``problem``，与这里的形状断言矛盾。
    """

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

    sdk_tool = tool_factory(ctx)
    assert sdk_tool.name in sdk_tools.MIGRATION_BLOCKED_TOOL_IDS
    guarded = sdk_tools._refuse_while_migration_failed(sdk_tool, ctx)  # pyright: ignore[reportPrivateUsage]
    blocked = await guarded.handler({})

    assert blocked["is_error"] is True
    assert blocked["problem"]["code"] == GenerationProblemCode.PROJECT_MIGRATION_FAILED
    assert blocked["problem"]["action"] == GenerationAction.RETRY_PROJECT_MIGRATION
    assert blocked["problem"]["detail"] == failure.reason
    assert "script_edit" not in blocked


async def test_mcp_guard_reads_the_session_projects_root_not_the_global_one(tmp_path: Path, monkeypatch) -> None:
    """守卫的裁决必须取自 ctx.pm：会话可能绑定另一个 projects_root，同名项目不是同一个项目。"""

    import lib.project_migration_guard as guard
    from server.agent_runtime import sdk_tools

    session_root = tmp_path / "session"
    session_root.mkdir()
    session_dir, *_ = _project(session_root)
    assert migrate_project_with_verdict(session_dir) is None

    global_root = tmp_path / "global"
    global_root.mkdir()
    global_dir, *_ = _project(global_root)
    _break_episode_script(global_dir)
    assert migrate_project_with_verdict(global_dir) is not None

    monkeypatch.setattr(guard, "get_project_manager", lambda: ProjectManager(str(global_root)))
    ctx = sdk_tools.ToolContext(project_name="demo", projects_root=session_root, pm=ProjectManager(str(session_root)))
    ran = False

    @tool("generate_storyboards", "stub", {"type": "object", "properties": {}})
    async def _inner(_args: dict[str, object]) -> dict[str, object]:
        nonlocal ran
        ran = True
        return {"content": []}

    guarded = sdk_tools._refuse_while_migration_failed(_inner, ctx)  # pyright: ignore[reportPrivateUsage]
    result = await guarded.handler({})

    assert ran is True
    assert result.get("is_error") is not True


def test_retry_keeps_the_project_blocked_when_the_chain_cannot_place_it(tmp_path: Path) -> None:
    """迁移器一次也没跑起来（project.json 不可读）时不得报成功——裁决必须留着。"""

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)
    assert migrate_project_with_verdict(project_dir) is not None

    # 「修复」把 project.json 弄没了：链条无处落脚，schema 仍未达标。
    (project_dir / "project.json").unlink()
    residual = migrate_project_with_verdict(project_dir)

    assert residual is not None
    assert str(CURRENT_PROJECT_SCHEMA_VERSION) in residual.reason
    assert load_migration_failure(project_dir) is not None


def test_a_verdict_that_cannot_be_persisted_fails_loud(tmp_path: Path) -> None:
    """裁决写不进磁盘时，任何守卫都读不到它——报成功等于放开一个该被阻断的项目。"""

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)
    # 记录位置被一个目录占住：落盘那一步必然失败，且不依赖平台的权限语义。
    (project_dir / MIGRATION_FAILURE_FILENAME).mkdir()

    with pytest.raises(OSError):
        migrate_project_with_verdict(project_dir)

    # 启动期一个项目写不下裁决，不拖垮同一轮里其它项目：healthy 排在 demo 之后，
    # 只有循环真的继续了它才会被迁移。
    seed_root = tmp_path / "seed"
    seed_root.mkdir()
    healthy_dir, *_ = _project(seed_root)
    healthy_dir.rename(projects_root / "healthy")
    summary = run_project_migrations(projects_root)

    assert "demo" in summary.failed
    assert "healthy" in summary.migrated


def _guarded_app() -> FastAPI:
    """一个只挂守卫的最小 app：断言的是依赖本身在真实 FastAPI 栈里的行为。"""

    app = FastAPI()
    register_error_handlers(app)
    router = APIRouter(dependencies=[Depends(require_project_migration_ok)])

    @router.post("/projects/{project_name}/generate/thing")
    async def _generate(project_name: str) -> dict[str, str]:
        return {"generated": project_name}

    @router.get("/projects/{project_name}/thing")
    async def _read(project_name: str) -> dict[str, str]:
        return {"read": project_name}

    @router.post("/no-project-param")
    async def _unparametrized() -> dict[str, str]:
        return {"ok": "yes"}

    app.include_router(router)
    return app


def test_rest_guard_refuses_writes_but_keeps_reads_open(tmp_path: Path, monkeypatch) -> None:
    import lib.project_migration_guard as guard

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir, *_ = _project(projects_root)
    _break_episode_script(project_dir)
    assert migrate_project_with_verdict(project_dir) is not None
    monkeypatch.setattr(guard, "get_project_manager", lambda: ProjectManager(str(projects_root)))

    client = TestClient(_guarded_app())

    refused = client.post("/projects/demo/generate/thing")
    assert refused.status_code == 409
    assert "demo" in refused.json()["detail"]

    # 只读照常：被阻断的项目仍要能看脚本与画布上已有的产物
    assert client.get("/projects/demo/thing").status_code == 200

    _repair_episode_script(project_dir)
    assert migrate_project_with_verdict(project_dir) is None
    assert client.post("/projects/demo/generate/thing").status_code == 200


def test_rest_guard_fails_loud_when_the_route_has_no_project_param() -> None:
    client = TestClient(_guarded_app(), raise_server_exceptions=True)

    with pytest.raises(RuntimeError, match="没有项目路径参数"):
        client.post("/no-project-param")


def test_every_guarded_router_route_can_name_its_project() -> None:
    """守卫按路径参数取项目，所以挂了守卫的路由必须都带得出项目名——否则会 fail loud。"""

    from server.app import app

    for route in app.routes:
        dependencies = getattr(getattr(route, "dependant", None), "dependencies", ())
        if not any(dep.call is require_project_migration_ok for dep in dependencies):
            continue
        assert {"project_name", "name"} & set(getattr(route, "param_convertors", {})), route.path


def test_a_repaired_project_is_idempotent_to_retry(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)

    assert migrate_project_with_verdict(project_dir) is None
    # Already at the current schema: rerunning is a no-op success, not a second migration.
    assert migrate_project_with_verdict(project_dir) is None
    assert load_migration_failure(project_dir) is None
