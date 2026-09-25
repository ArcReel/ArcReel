"""按 Agent 工具集声明表驱动的双宿主一致性。

每条声明在 ArcReel Agent（内嵌 SDK server）与外部 Agent（远程 MCP）两侧投影出的名字、schema、
描述、迁移阻断与结果信封必须一致；允许的差异只有远程 schema 的 ``project`` 与长任务附注。
无 scope 声明（列出、创建项目）两侧都不带 ``project``。
领域行为在 handler 的 ``ToolOutcome`` 层测，这里的 fake handler 只用于观察 adapter 是否原样透传。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp import types
from mcp.server import Server

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_result import GenerationBatchResult
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_failure import (
    MIGRATION_FAILURE_CODE,
    RETRY_MIGRATION_ACTION,
    record_migration_failure,
)
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS, MIGRATION_BLOCKED_TOOL_IDS, build_arcreel_mcp_server
from server.agent_toolset.declaration import (
    BLOCKED,
    AgentToolDeclaration,
    Blocked,
    ToolDeclaration,
    UnscopedToolDeclaration,
)
from server.agent_toolset.embedded import embedded_server
from server.agent_toolset.envelope import json_value
from server.agent_toolset.episode_planning import PLAN_EPISODES, RESET_EPISODE_PLANNING
from server.agent_toolset.generation_batches import CANCEL_GENERATION_BATCH, GET_GENERATION_BATCH
from server.agent_toolset.grid_storyboards import GENERATE_GRID, SPLIT_GRIDS
from server.agent_toolset.orientation import GET_PROMPT_PREVIEW, GET_VIDEO_CAPABILITIES
from server.agent_toolset.project_entry import CREATE_PROJECT
from server.agent_toolset.remote import LONG_TASK_NOTE, remote_tool
from server.agent_toolset.script_editing import PATCH_EPISODE_SCRIPT
from server.agent_toolset.toolset import AGENT_TOOLSET
from server.agent_toolset.workflow_completion import COMPLETE_ASSET_INVENTORY, COMPLETE_SCRIPT_PLAN_REBUILD
from server.remote_mcp import build_remote_mcp_server
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import (
    CallerContext,
    GenerationBatchToolRequest,
    ProjectScope,
    Services,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    get_generation_batch,
)

# 每条声明一份合法入参；新增声明须在此登记，否则参数化用例以 KeyError 失败。
SAMPLE_ARGUMENTS: dict[str, dict[str, Any]] = {
    "list_projects": {},
    "create_project": {"name": "fresh", "title": "Fresh"},
    "upload_source": {"filename": "novel.txt", "content": "第一章\n你好", "on_conflict": "replace"},
    "get_workflow_plan": {"episode": 1},
    "get_video_capabilities": {},
    "get_prompt_preview": {"script": "episode_1.json", "item_id": "E1S01"},
    "get_generation_batch": {"batch_id": "batch-absent"},
    "cancel_generation_batch": {"batch_id": "batch-absent"},
    "patch_project": {"overview": {"synopsis": "梗概"}},
    "patch_episode_meta": {"script": "episode_1.json", "field": "title", "value": "第一集"},
    "rename_asset": {"table": "characters", "old_name": "甲", "new_name": "乙"},
    "retry_project_migration": {},
    "get_project_content": {},
    "list_source_files": {},
    "get_source_text": {"path": "source/episode_1.txt"},
    "get_episode_script": {"script": "episode_1.json"},
    "get_script_plan_content": {"episode": 1},
    "list_project_files": {},
    "read_project_file": {"path": "project.json"},
    "list_pending_assets": {"type": "character"},
    "generate_assets": {"type": "character", "names": ["张三"]},
    "generate_storyboards": {"script": "episode_1.json"},
    "edit_images": {"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]},
    "generate_narration_audio": {"script": "episode_1.json", "segment_ids": ["E1S01"]},
    "patch_episode_script": {
        "script": "episode_9.json",
        "base_revision": "sha256-v1:" + "0" * 64,
        "operations": [{"op": "remove", "id": "E9S01"}],
    },
    "generate_grid": {"script": "episode_1.json", "list_only": True},
    "split_grids": {"grid_ids": ["grid_000000000000"]},
    "plan_episodes": {"instructions": "按章节对齐切分"},
    "reset_episode_planning": {"from_episode": 1},
    "complete_asset_inventory": {
        "scope": {"kind": "all", "files": []},
        "expected_source_revision": "sha256-v1:" + "0" * 64,
    },
    "complete_script_plan_rebuild": {"episode": 1, "expected_stale_script_plan_revision": None},
    "generate_videos": {
        "script": "episode_1.json",
        "target": {"scope": "all"},
        "narration_delivery": "post_production",
    },
}

_DECLARATIONS = pytest.mark.parametrize("declaration", AGENT_TOOLSET, ids=lambda declaration: declaration.name)
_SCOPED_DECLARATIONS = pytest.mark.parametrize(
    "declaration",
    [declaration for declaration in AGENT_TOOLSET if isinstance(declaration, ToolDeclaration)],
    ids=lambda declaration: declaration.name,
)


@pytest.fixture
def projects(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "data")
    root = manager.projects_dir
    manager.create_project("demo", content_mode="drama")
    manager.create_project_metadata("demo", "Demo", "", "drama")
    manager.upsert_assets("demo", "characters", {"甲": {"description": "主角"}})
    project_dir = root / "demo"
    (project_dir / "source").mkdir(exist_ok=True)
    (project_dir / "source" / "episode_1.txt").write_text("第一集原文", encoding="utf-8")
    (project_dir / "scripts").mkdir(exist_ok=True)
    (project_dir / "scripts" / "episode_1.json").write_text('{"episode":1,"scenes":[]}', encoding="utf-8")
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "script_plan_normalized_script.json").write_text('{"title":"第一集","scenes":[]}', encoding="utf-8")
    (root / "empty").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "project.json").write_text("{}", encoding="utf-8")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    return manager


@pytest.fixture
def services(projects: ProjectManager) -> Services:
    return Services(
        projects=projects,
        workflow_planner=WorkflowPlanner(projects),
        capabilities=ConfigResolver(async_session_factory),
    )


def _scope(projects: ProjectManager) -> ProjectScope:
    return ProjectScope(project_name="demo", data_root=projects.data_root)


def _answering[DeclarationT: AgentToolDeclaration](
    declaration: DeclarationT, outcome: ToolOutcome[Any], calls: list[object]
) -> DeclarationT:
    if isinstance(declaration, UnscopedToolDeclaration):

        async def unscoped_handler(request, _caller, _services) -> ToolOutcome[Any]:
            calls.append(request.value)
            return outcome

        return replace(declaration, handler=unscoped_handler)

    async def handler(request, _scope, _caller, _services) -> ToolOutcome[Any]:
        calls.append(request.value)
        return outcome

    return replace(declaration, handler=handler)


def _remote_arguments(declaration: AgentToolDeclaration, arguments: dict[str, Any]) -> dict[str, Any]:
    """外部 Agent 的等价入参：作用于项目的工具显式带上 ``project``。"""
    if isinstance(declaration, UnscopedToolDeclaration):
        return dict(arguments)
    return {"project": "demo", **arguments}


async def _call_server(server: Server, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    """经 in-process server 的请求入口调用，拿到的就是模型能看到的 content 与 isError。"""
    request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=arguments))
    result = (await server.request_handlers[types.CallToolRequest](request)).root
    assert isinstance(result, types.CallToolResult)
    return result


async def _call_embedded(
    declaration: AgentToolDeclaration, arguments: dict[str, Any], services: Services
) -> types.CallToolResult:
    server = embedded_server(
        [declaration],
        name="arcreel",
        version="1.0.0",
        scope=_scope(services.projects),
        caller=CallerContext(user_id="u1", source="embedded"),
        services=services,
    )["instance"]
    return await _call_server(server, declaration.name, arguments)


async def _call_remote(
    declaration: AgentToolDeclaration, arguments: dict[str, Any], services: Services
) -> types.CallToolResult:
    tool = remote_tool(
        declaration,
        projects=services.projects,
        services=services,
        caller=lambda: CallerContext(user_id="u1", source="mcp"),
    )
    result = await tool.run(arguments)
    assert isinstance(result, types.CallToolResult)
    return result


def _texts(result: types.CallToolResult) -> list[str]:
    return [block.text for block in result.content if isinstance(block, types.TextContent)]


def _embedded_json(result: types.CallToolResult) -> Any:
    return json.loads(_texts(result)[-1])


def _without_project(schema: dict[str, Any]) -> dict[str, Any]:
    stripped = {**schema, "properties": {k: v for k, v in schema["properties"].items() if k != "project"}}
    required = [name for name in schema.get("required", []) if name != "project"]
    if required:
        stripped["required"] = required
    else:
        stripped.pop("required", None)
    return stripped


async def _embedded_listing(projects: ProjectManager) -> dict[str, types.Tool]:
    server = build_arcreel_mcp_server(project_name="demo", data_root=projects.data_root)["instance"]
    result = (await server.request_handlers[types.ListToolsRequest](types.ListToolsRequest(method="tools/list"))).root
    assert isinstance(result, types.ListToolsResult)
    return {tool.name: tool for tool in result.tools}


@_SCOPED_DECLARATIONS
async def test_both_hosts_expose_the_declared_name_schema_and_description(
    declaration: ToolDeclaration[Any, Any], projects: ProjectManager, services: Services
) -> None:
    embedded = (await _embedded_listing(projects))[declaration.name]
    remote = {tool.name: tool for tool in await build_remote_mcp_server(services=services).list_tools()}[
        declaration.name
    ]

    assert declaration.name in ARCREEL_MCP_TOOL_IDS
    assert "project" not in embedded.inputSchema["properties"]
    assert remote.inputSchema["properties"]["project"]["type"] == "string"
    assert remote.inputSchema["required"][0] == "project"
    assert _without_project(remote.inputSchema) == embedded.inputSchema
    assert all(spec.get("description") for spec in embedded.inputSchema["properties"].values())
    assert embedded.description == declaration.description
    assert remote.description == declaration.description + (LONG_TASK_NOTE if declaration.long_task else "")


@pytest.mark.parametrize(
    "declaration",
    [declaration for declaration in AGENT_TOOLSET if isinstance(declaration, UnscopedToolDeclaration)],
    ids=lambda declaration: declaration.name,
)
async def test_both_hosts_expose_an_unscoped_declaration_without_project(
    declaration: UnscopedToolDeclaration[Any, Any], projects: ProjectManager, services: Services
) -> None:
    embedded = (await _embedded_listing(projects))[declaration.name]
    remote = {tool.name: tool for tool in await build_remote_mcp_server(services=services).list_tools()}[
        declaration.name
    ]

    assert declaration.name in ARCREEL_MCP_TOOL_IDS
    assert "project" not in remote.inputSchema["properties"]
    assert remote.inputSchema == embedded.inputSchema == declaration.input_schema
    assert all(spec.get("description") for spec in embedded.inputSchema["properties"].values())
    assert embedded.description == remote.description == declaration.description


def test_migration_blocked_ids_follow_each_declared_policy() -> None:
    assert {
        declaration.name
        for declaration in AGENT_TOOLSET
        if isinstance(declaration, ToolDeclaration) and isinstance(declaration.migration, Blocked)
    } == {declaration.name for declaration in AGENT_TOOLSET if declaration.name in MIGRATION_BLOCKED_TOOL_IDS}


@_DECLARATIONS
async def test_problems_pass_through_both_hosts_unchanged(
    declaration: AgentToolDeclaration, services: Services
) -> None:
    problem = ToolProblem("sentinel_problem", "原样透传", action="sentinel_action", params={"ids": ["E1S01"]})
    answering = _answering(declaration, ToolOutcome(problem=problem), [])

    embedded = await _call_embedded(answering, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(answering, _remote_arguments(answering, SAMPLE_ARGUMENTS[declaration.name]), services)

    expected = {
        "problem": {
            "code": "sentinel_problem",
            "detail": "原样透传",
            "action": "sentinel_action",
            "params": {"ids": ["E1S01"]},
        }
    }
    assert embedded.isError is True
    assert remote.isError is True
    assert _embedded_json(embedded) == remote.structuredContent == expected
    assert _texts(embedded) == _texts(remote)


@_DECLARATIONS
async def test_invalid_arguments_are_rejected_as_the_same_invalid_request_by_both_hosts(
    declaration: AgentToolDeclaration, projects: ProjectManager, services: Services
) -> None:
    arguments = {**SAMPLE_ARGUMENTS[declaration.name], "unexpected": 1}
    # 经会话真实注册的 in-process server 调用：MCP 层不做 schema 预校验，坏参数由请求模型拒绝。
    session_server = build_arcreel_mcp_server(project_name="demo", data_root=projects.data_root)["instance"]

    embedded = await _call_server(session_server, declaration.name, arguments)
    remote = await _call_remote(declaration, _remote_arguments(declaration, arguments), services)

    assert embedded.isError is True
    assert remote.isError is True
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
    assert remote.structuredContent is not None
    assert remote.structuredContent["problem"]["code"] == "invalid_request"
    assert remote.structuredContent["problem"]["params"]["errors"][0]["loc"] == ["unexpected"]


@_SCOPED_DECLARATIONS
async def test_a_blocked_declaration_refuses_a_migration_failed_project_at_both_entries(
    declaration: ToolDeclaration[Any, Any], projects: ProjectManager, services: Services
) -> None:
    record_migration_failure(
        projects.get_project_path("demo"), RuntimeError("清单预检失败"), schema_version=CURRENT_PROJECT_SCHEMA_VERSION
    )
    calls: list[object] = []
    blocked = _answering(replace(declaration, migration=BLOCKED), ToolOutcome(value={}), calls)

    embedded = await _call_embedded(blocked, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(blocked, {"project": "demo", **SAMPLE_ARGUMENTS[declaration.name]}, services)

    assert calls == []
    assert embedded.isError is True
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
    assert remote.structuredContent is not None
    problem = remote.structuredContent["problem"]
    assert problem["code"] == MIGRATION_FAILURE_CODE
    assert problem["action"] == RETRY_MIGRATION_ACTION
    assert problem["params"]["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert len(_texts(embedded)) == 2


@pytest.mark.parametrize(
    "declaration",
    [
        declaration
        for declaration in AGENT_TOOLSET
        if not (isinstance(declaration, ToolDeclaration) and isinstance(declaration.migration, Blocked))
    ],
    ids=lambda declaration: declaration.name,
)
async def test_unblocked_declarations_reach_the_handler_on_a_migration_failed_project(
    declaration: AgentToolDeclaration, projects: ProjectManager, services: Services
) -> None:
    record_migration_failure(
        projects.get_project_path("demo"), RuntimeError("清单预检失败"), schema_version=CURRENT_PROJECT_SCHEMA_VERSION
    )
    answering = _answering(declaration, ToolOutcome(value={"answered": True}), [])

    embedded = await _call_embedded(answering, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(answering, _remote_arguments(answering, SAMPLE_ARGUMENTS[declaration.name]), services)

    assert _embedded_json(embedded) == remote.structuredContent == {declaration.domain_key: {"answered": True}}


@pytest.mark.parametrize(
    "declaration",
    [PLAN_EPISODES, RESET_EPISODE_PLANNING, COMPLETE_ASSET_INVENTORY, COMPLETE_SCRIPT_PLAN_REBUILD],
    ids=lambda declaration: declaration.name,
)
async def test_planning_and_completion_entries_refuse_a_migration_failed_project_before_the_handler(
    declaration: ToolDeclaration[Any, Any], projects: ProjectManager, services: Services
) -> None:
    """这些入口按声明阻断，不依赖 handler 内层的迁移兜底。"""
    record_migration_failure(
        projects.get_project_path("demo"), RuntimeError("清单预检失败"), schema_version=CURRENT_PROJECT_SCHEMA_VERSION
    )
    calls: list[object] = []
    answering = _answering(declaration, ToolOutcome(value={}), calls)

    embedded = await _call_embedded(answering, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(answering, {"project": "demo", **SAMPLE_ARGUMENTS[declaration.name]}, services)

    assert declaration.name in MIGRATION_BLOCKED_TOOL_IDS
    assert calls == []
    assert _embedded_json(embedded) == remote.structuredContent
    assert remote.structuredContent is not None
    problem = remote.structuredContent["problem"]
    assert problem["code"] == MIGRATION_FAILURE_CODE
    assert problem["action"] == RETRY_MIGRATION_ACTION


async def test_episode_script_reader_reports_the_same_migration_problem_in_both_hosts(
    projects: ProjectManager, services: Services
) -> None:
    record_migration_failure(
        projects.get_project_path("demo"), RuntimeError("清单预检失败"), schema_version=CURRENT_PROJECT_SCHEMA_VERSION
    )
    declaration = next(declaration for declaration in AGENT_TOOLSET if declaration.name == "get_episode_script")

    embedded = await _call_embedded(declaration, {"script": "episode_1.json"}, services)
    remote = await _call_remote(declaration, {"project": "demo", "script": "episode_1.json"}, services)

    assert embedded.isError is True
    assert remote.isError is True
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
    assert remote.structuredContent is not None
    problem = remote.structuredContent["problem"]
    assert problem["code"] == MIGRATION_FAILURE_CODE
    assert problem["action"] == RETRY_MIGRATION_ACTION
    assert problem["params"]["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION


async def test_patch_episode_script_reports_a_missing_script_as_script_not_found_in_both_hosts(
    projects: ProjectManager, services: Services, tmp_path: Path
) -> None:
    arguments = SAMPLE_ARGUMENTS[PATCH_EPISODE_SCRIPT.name]

    embedded = await _call_embedded(PATCH_EPISODE_SCRIPT, arguments, services)
    remote = await _call_remote(
        PATCH_EPISODE_SCRIPT, _remote_arguments(PATCH_EPISODE_SCRIPT, arguments), _twin_services(projects, tmp_path)
    )

    assert embedded.isError is True
    assert remote.isError is True
    assert _embedded_json(embedded) == remote.structuredContent
    assert remote.structuredContent is not None
    assert remote.structuredContent["problem"]["code"] == "script_not_found"


def _twin_services(projects: ProjectManager, tmp_path: Path) -> Services:
    """同一初始状态的另一份项目根，让写入类工具在两宿主各跑一次、互不影响。"""
    root = tmp_path / "twin"
    shutil.copytree(projects.data_root, root, symlinks=True)
    twin = ProjectManager(root)
    return Services(
        projects=twin, workflow_planner=WorkflowPlanner(twin), capabilities=ConfigResolver(async_session_factory)
    )


# 真实 handler 的结果随调用时刻变化，两次调用无法逐字比较；透传一致性由其余用例的 fake handler 覆盖。
_TIME_DEPENDENT_RESULTS = frozenset({CREATE_PROJECT.name})

# 样例入参下合法地返回 problem 的声明：测试项目缺少它们要找的对象或能力配置。其余声明在样例入参下必须成功。
_PROBLEM_ON_SAMPLE = frozenset(
    {
        GET_VIDEO_CAPABILITIES.name,
        GET_PROMPT_PREVIEW.name,
        GET_GENERATION_BATCH.name,
        CANCEL_GENERATION_BATCH.name,
        PATCH_EPISODE_SCRIPT.name,
        SPLIT_GRIDS.name,
        COMPLETE_ASSET_INVENTORY.name,
        COMPLETE_SCRIPT_PLAN_REBUILD.name,
    }
)


@pytest.mark.parametrize(
    "declaration",
    [
        declaration
        for declaration in AGENT_TOOLSET
        if not (isinstance(declaration, ToolDeclaration) and declaration.long_task)
        and declaration.name not in _TIME_DEPENDENT_RESULTS
    ],
    ids=lambda declaration: declaration.name,
)
async def test_embedded_content_carries_the_same_json_as_remote_structured_content(
    declaration: AgentToolDeclaration, projects: ProjectManager, services: Services, tmp_path: Path
) -> None:
    twin = _twin_services(projects, tmp_path)
    embedded = await _call_embedded(declaration, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(declaration, _remote_arguments(declaration, SAMPLE_ARGUMENTS[declaration.name]), twin)

    assert embedded.isError is remote.isError is (declaration.name in _PROBLEM_ON_SAMPLE)
    assert remote.structuredContent is not None
    assert set(remote.structuredContent) == {"problem" if remote.isError else declaration.domain_key}
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)


async def test_a_summary_precedes_the_structured_json_in_both_hosts(services: Services) -> None:
    declaration = next(declaration for declaration in AGENT_TOOLSET if declaration.name == "get_project_content")
    summarized = replace(
        _answering(declaration, ToolOutcome(value={"title": "Demo"}), []),
        summary=lambda value: f"已读取《{value['title']}》",
    )

    embedded = await _call_embedded(summarized, {}, services)
    remote = await _call_remote(summarized, {"project": "demo"}, services)

    assert _texts(embedded) == _texts(remote) == ["已读取《Demo》", '{"project_content": {"title": "Demo"}}']
    assert remote.structuredContent == {"project_content": {"title": "Demo"}}


@_SCOPED_DECLARATIONS
@pytest.mark.parametrize(
    "project",
    [pytest.param(None, id="missing"), 7, "", "   ", "../demo", "demo/..", "absent", "empty", "escape"],
)
async def test_remote_project_locating_failures_are_invalid_project(
    declaration: ToolDeclaration[Any, Any], project: object, services: Services
) -> None:
    calls: list[object] = []
    answering = _answering(declaration, ToolOutcome(value={}), calls)
    arguments = dict(SAMPLE_ARGUMENTS[declaration.name])
    if project is not None:
        arguments["project"] = project

    result = await _call_remote(answering, arguments, services)

    assert calls == []
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["problem"]["code"] == "invalid_project"


async def test_embedded_terminal_generation_result_has_the_shape_remote_polling_reads_at_the_terminal_state(
    projects: ProjectManager, services: Services, db_factory
) -> None:
    """长任务是唯一允许的结果差异：内嵌拿终态结果，远程拿批次句柄；两边的终态生成结果同形。"""
    project = projects.load_project("demo")
    project["characters"] = {"李四": {"description": ""}}
    projects.save_project("demo", project)
    services = replace(services, queue=GenerationQueue(session_factory=db_factory, project_manager=projects))
    declaration = next(declaration for declaration in AGENT_TOOLSET if declaration.name == "generate_assets")
    arguments = {"type": "character", "names": ["李四"]}
    waiter = AsyncMock(return_value=([], []))
    embedded_caller = CallerContext(user_id="u1", source="embedded", batch_waiter=waiter)
    remote_caller = CallerContext(user_id="u1", source="mcp")

    session_server = embedded_server(
        [declaration],
        name="arcreel",
        version="1.0.0",
        scope=_scope(projects),
        caller=embedded_caller,
        services=services,
    )["instance"]
    embedded = await _call_server(session_server, declaration.name, arguments)
    remote = await remote_tool(declaration, projects=projects, services=services, caller=lambda: remote_caller).run(
        {"project": "demo", **arguments}
    )
    assert isinstance(remote, types.CallToolResult)
    assert remote.structuredContent is not None
    handle = remote.structuredContent["generation_batch"]
    polled = await get_generation_batch(
        ToolRequest(GenerationBatchToolRequest(batch_id=handle["batch_id"])), _scope(projects), remote_caller, services
    )

    # 内嵌：摘要在前、终态结果 JSON 在后；批次全部被阻断即结果级失败。
    terminal = _embedded_json(embedded)
    assert embedded.isError is True
    assert len(_texts(embedded)) == 2
    assert set(terminal) == {"generation_result", "batch_id"}
    # 远程：立即拿到批次句柄，按句柄轮询到终态后读取附带的生成结果。
    assert remote.isError is False
    assert set(remote.structuredContent) == {"generation_batch"}
    assert polled.value is not None
    assert polled.value.done is True
    assert polled.value.generation_result is not None
    polled_result = json_value(polled.value.generation_result)
    assert GenerationBatchResult.model_validate(terminal["generation_result"]).blocked == ["character/李四"]
    assert terminal["generation_result"] == polled_result
    waiter.assert_not_awaited()


async def test_grid_list_only_preview_is_the_same_json_in_both_hosts(
    projects: ProjectManager, services: Services
) -> None:
    """宫格预览不是生成结果：两宿主都立即拿到同一份规划文本，放在工具名下，没有批次可轮询。"""
    project = projects.load_project("demo")
    project.update(
        {
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
        }
    )
    projects.save_project("demo", project)
    scenes = [{"scene_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)]
    (projects.get_project_path("demo") / "scripts" / "episode_1.json").write_text(
        json.dumps({"episode": 1, "content_mode": "drama", "scenes": scenes}), encoding="utf-8"
    )
    arguments = SAMPLE_ARGUMENTS[GENERATE_GRID.name]

    embedded = await _call_embedded(GENERATE_GRID, arguments, services)
    remote = await _call_remote(GENERATE_GRID, _remote_arguments(GENERATE_GRID, arguments), services)

    assert embedded.isError is remote.isError is False
    assert remote.structuredContent is not None
    assert set(remote.structuredContent) == {GENERATE_GRID.name}
    assert "E1S01..E1S04" in remote.structuredContent[GENERATE_GRID.name]
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
