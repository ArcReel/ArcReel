"""外部 Agent（远程 MCP）adapter。

schema 头部追加必填 ``project``，每次调用显式定位项目；任何定位失败统一返回 ``invalid_project``。
无 scope 声明（列出、创建项目）不追加 ``project``，也不定位项目。
长任务声明在描述末尾追加统一的批次句柄与轮询说明。结果写进 ``structuredContent``，content 与内嵌宿主
的文本块相同。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from copy import deepcopy
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp.tools import Tool as FastMCPTool
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from lib.db.base import DEFAULT_USER_ID
from lib.project.project_manager import ProjectManager
from server.agent_toolset.declaration import (
    AgentToolDeclaration,
    ToolDeclaration,
    UnscopedToolDeclaration,
    invoke_declaration,
    invoke_unscoped_declaration,
    tool_description,
)
from server.agent_toolset.envelope import encode_outcome
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolProblem

PROJECT_PROPERTY: dict[str, Any] = {
    "type": "string",
    "description": "目标项目的项目名（list_projects 返回的 name）；每次调用都须显式指定",
}

LONG_TASK_NOTE = (
    "\n\n外部调用不等待生成完成：提交后立即返回批次句柄 generation_batch，"
    "按其中的 poll_after_seconds 间隔调用 get_generation_batch 轮询，直到 done=true 再读取终态结果。"
)


def authenticated_caller() -> CallerContext:
    token = get_access_token()
    if token is None:
        raise RuntimeError("authenticated MCP request has no access token")
    # API-key subjects identify credentials; the supported single-operator model persists queue ownership as default.
    return CallerContext(user_id=DEFAULT_USER_ID, source="mcp")


def resolve_project_scope(project: object, projects: ProjectManager) -> ProjectScope:
    """规范化并定位 ``project``；非法、越界、不存在或缺 project.json 时抛 ``ValueError`` / ``FileNotFoundError``。"""
    if not isinstance(project, str):
        raise ValueError("project 必须是项目名字符串")
    project_name = projects.normalize_project_name(project)
    projects.get_project_path(project_name)
    if not projects.project_exists(project_name):
        raise FileNotFoundError(f"项目 '{project_name}' 缺少 project.json")
    return ProjectScope(project_name=project_name, data_root=projects.data_root)


def remote_input_schema(declaration: AgentToolDeclaration) -> dict[str, Any]:
    schema = deepcopy(declaration.input_schema)
    if isinstance(declaration, UnscopedToolDeclaration):
        return schema
    schema["properties"] = {"project": PROJECT_PROPERTY, **schema["properties"]}
    schema["required"] = ["project", *schema.get("required", [])]
    return schema


def remote_description(declaration: AgentToolDeclaration) -> str:
    description = tool_description(declaration)
    if isinstance(declaration, ToolDeclaration) and declaration.long_task:
        return description + LONG_TASK_NOTE
    return description


def remote_result(declaration: AgentToolDeclaration, outcome: ToolOutcome[Any]) -> CallToolResult:
    envelope = encode_outcome(declaration, outcome)
    return CallToolResult(
        content=[TextContent(type="text", text=text) for text in envelope.texts],
        structuredContent=envelope.structured,
        isError=envelope.is_error,
    )


class _DeclaredRemoteTool(FastMCPTool):
    """绕过 FastMCP 的签名推导：参数由声明的请求模型校验，``project`` 由本 adapter 定位。"""

    invoke: Callable[[dict[str, Any]], Awaitable[CallToolResult]] = Field(exclude=True)

    async def run(self, arguments: dict[str, Any], context: Any = None, convert_result: bool = False) -> Any:
        del context, convert_result
        return await self.invoke(arguments)


def remote_tool(
    declaration: AgentToolDeclaration,
    *,
    projects: ProjectManager,
    services: Services,
    caller: Callable[[], CallerContext] = authenticated_caller,
) -> FastMCPTool:
    async def invoke(arguments: dict[str, Any]) -> CallToolResult:
        if isinstance(declaration, UnscopedToolDeclaration):
            return remote_result(
                declaration, await invoke_unscoped_declaration(declaration, arguments, caller(), services)
            )
        request_arguments = dict(arguments)
        try:
            scope = resolve_project_scope(request_arguments.pop("project", None), projects)
        except (FileNotFoundError, ValueError) as exc:
            return remote_result(declaration, ToolOutcome(problem=ToolProblem("invalid_project", str(exc))))
        return remote_result(
            declaration, await invoke_declaration(declaration, request_arguments, scope, caller(), services)
        )

    async def unused() -> CallToolResult:
        raise RuntimeError("declared remote tools override run")

    metadata = FastMCPTool.from_function(unused, structured_output=False)
    return _DeclaredRemoteTool(
        fn=unused,
        name=declaration.name,
        title=None,
        description=remote_description(declaration),
        parameters=remote_input_schema(declaration),
        fn_metadata=metadata.fn_metadata,
        is_async=True,
        context_kwarg=None,
        annotations=None,
        invoke=invoke,
    )


def remote_tools(
    declarations: Iterable[AgentToolDeclaration],
    *,
    projects: ProjectManager,
    services: Services,
) -> list[FastMCPTool]:
    return [remote_tool(declaration, projects=projects, services=services) for declaration in declarations]


__all__ = [
    "LONG_TASK_NOTE",
    "PROJECT_PROPERTY",
    "authenticated_caller",
    "remote_description",
    "remote_input_schema",
    "remote_result",
    "remote_tool",
    "remote_tools",
    "resolve_project_scope",
]
