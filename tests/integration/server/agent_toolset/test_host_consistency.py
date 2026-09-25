"""按 Agent 工具集声明表驱动的双宿主一致性。

每条声明在 ArcReel Agent（内嵌 SDK server）与外部 Agent（远程 MCP）两侧投影出的名字、schema、
描述、迁移阻断与结果信封必须一致；允许的差异只有远程 schema 的 ``project`` 与长任务附注。
领域行为在 handler 的 ``ToolOutcome`` 层测，这里的 fake handler 只用于观察 adapter 是否原样透传。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp import types
from mcp.server import Server

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_failure import (
    MIGRATION_FAILURE_CODE,
    RETRY_MIGRATION_ACTION,
    record_migration_failure,
)
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS, MIGRATION_BLOCKED_TOOL_IDS, build_arcreel_mcp_server
from server.agent_toolset.declaration import BLOCKED, Blocked, ToolDeclaration
from server.agent_toolset.embedded import embedded_server
from server.agent_toolset.remote import LONG_TASK_NOTE, remote_tool
from server.agent_toolset.toolset import AGENT_TOOLSET
from server.remote_mcp import build_remote_mcp_server
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolProblem

# 每条声明一份合法入参；新增声明须在此登记，否则参数化用例以 KeyError 失败。
SAMPLE_ARGUMENTS: dict[str, dict[str, Any]] = {
    "get_project_content": {},
    "list_source_files": {},
    "get_source_text": {"path": "source/episode_1.txt"},
    "get_episode_script": {"script": "episode_1.json"},
    "get_script_plan_content": {"episode": 1},
    "list_project_files": {},
    "read_project_file": {"path": "project.json"},
}

_DECLARATIONS = pytest.mark.parametrize("declaration", AGENT_TOOLSET, ids=lambda declaration: declaration.name)


@pytest.fixture
def projects(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "data")
    root = manager.projects_dir
    manager.create_project("demo", content_mode="drama")
    manager.create_project_metadata("demo", "Demo", "", "drama")
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


def _answering(
    declaration: ToolDeclaration[Any, Any], outcome: ToolOutcome[Any], calls: list[object]
) -> ToolDeclaration[Any, Any]:
    async def handler(request, _scope, _caller, _services) -> ToolOutcome[Any]:
        calls.append(request.value)
        return outcome

    return replace(declaration, handler=handler)


async def _call_server(server: Server, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    """经 in-process server 的请求入口调用，拿到的就是模型能看到的 content 与 isError。"""
    request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=arguments))
    result = (await server.request_handlers[types.CallToolRequest](request)).root
    assert isinstance(result, types.CallToolResult)
    return result


async def _call_embedded(
    declaration: ToolDeclaration[Any, Any], arguments: dict[str, Any], services: Services
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
    declaration: ToolDeclaration[Any, Any], arguments: dict[str, Any], services: Services
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


@_DECLARATIONS
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


def test_migration_blocked_ids_follow_each_declared_policy() -> None:
    assert {declaration.name for declaration in AGENT_TOOLSET if isinstance(declaration.migration, Blocked)} == {
        declaration.name for declaration in AGENT_TOOLSET if declaration.name in MIGRATION_BLOCKED_TOOL_IDS
    }


@_DECLARATIONS
async def test_problems_pass_through_both_hosts_unchanged(
    declaration: ToolDeclaration[Any, Any], services: Services
) -> None:
    problem = ToolProblem("sentinel_problem", "原样透传", action="sentinel_action", params={"ids": ["E1S01"]})
    answering = _answering(declaration, ToolOutcome(problem=problem), [])

    embedded = await _call_embedded(answering, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(answering, {"project": "demo", **SAMPLE_ARGUMENTS[declaration.name]}, services)

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
    declaration: ToolDeclaration[Any, Any], projects: ProjectManager, services: Services
) -> None:
    arguments = {**SAMPLE_ARGUMENTS[declaration.name], "unexpected": 1}
    # 经会话真实注册的 in-process server 调用：MCP 层不做 schema 预校验，坏参数由请求模型拒绝。
    session_server = build_arcreel_mcp_server(project_name="demo", data_root=projects.data_root)["instance"]

    embedded = await _call_server(session_server, declaration.name, arguments)
    remote = await _call_remote(declaration, {"project": "demo", **arguments}, services)

    assert embedded.isError is True
    assert remote.isError is True
    assert _embedded_json(embedded) == remote.structuredContent
    assert _texts(embedded) == _texts(remote)
    assert remote.structuredContent is not None
    assert remote.structuredContent["problem"]["code"] == "invalid_request"
    assert remote.structuredContent["problem"]["params"]["errors"][0]["loc"] == ["unexpected"]


@_DECLARATIONS
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
    [declaration for declaration in AGENT_TOOLSET if not isinstance(declaration.migration, Blocked)],
    ids=lambda declaration: declaration.name,
)
async def test_unblocked_declarations_reach_the_handler_on_a_migration_failed_project(
    declaration: ToolDeclaration[Any, Any], projects: ProjectManager, services: Services
) -> None:
    record_migration_failure(
        projects.get_project_path("demo"), RuntimeError("清单预检失败"), schema_version=CURRENT_PROJECT_SCHEMA_VERSION
    )
    answering = _answering(declaration, ToolOutcome(value={"answered": True}), [])

    embedded = await _call_embedded(answering, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(answering, {"project": "demo", **SAMPLE_ARGUMENTS[declaration.name]}, services)

    assert _embedded_json(embedded) == remote.structuredContent == {declaration.domain_key: {"answered": True}}


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


@pytest.mark.parametrize(
    "declaration",
    [declaration for declaration in AGENT_TOOLSET if not declaration.long_task],
    ids=lambda declaration: declaration.name,
)
async def test_embedded_content_carries_the_same_json_as_remote_structured_content(
    declaration: ToolDeclaration[Any, Any], services: Services
) -> None:
    embedded = await _call_embedded(declaration, SAMPLE_ARGUMENTS[declaration.name], services)
    remote = await _call_remote(declaration, {"project": "demo", **SAMPLE_ARGUMENTS[declaration.name]}, services)

    assert embedded.isError is False
    assert remote.isError is False
    assert remote.structuredContent is not None
    assert set(remote.structuredContent) == {declaration.domain_key}
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


@_DECLARATIONS
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
