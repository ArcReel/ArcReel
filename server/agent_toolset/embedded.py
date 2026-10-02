"""ArcReel Agent（内嵌 Claude Agent SDK）adapter。

项目由会话决定，schema 不含 ``project``；无 scope 声明不接会话项目。结构化结果以 JSON 文本块写进 content、
排在摘要之后，不依赖 ``structuredContent`` 是否会被转给模型；图片块（如有）排在最后。

内嵌 server 关闭 MCP 层的 inputSchema 预校验：已声明工具的参数一律由请求模型校验，
坏参数与远程宿主一样得到 ``invalid_request`` problem，而不是 MCP 的纯文本校验错误。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from claude_agent_sdk import McpSdkServerConfig
from mcp import types
from mcp.server import Server

from server.agent_toolset.declaration import (
    AgentToolDeclaration,
    UnscopedToolDeclaration,
    invoke_declaration,
    invoke_unscoped_declaration,
    tool_description,
)
from server.agent_toolset.envelope import encode_outcome
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome


def embedded_result(declaration: AgentToolDeclaration, outcome: ToolOutcome[Any]) -> types.CallToolResult:
    envelope = encode_outcome(declaration, outcome)
    return types.CallToolResult(
        content=[
            *(types.TextContent(type="text", text=text) for text in envelope.texts),
            *(
                types.ImageContent(type="image", data=image.base64_data, mimeType=image.mime_type)
                for image in envelope.images
            ),
        ],
        isError=envelope.is_error,
    )


def embedded_server(
    declarations: Iterable[AgentToolDeclaration],
    *,
    name: str,
    version: str,
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> McpSdkServerConfig:
    """以会话项目暴露已声明工具的 in-process MCP server。"""
    declared = {declaration.name: declaration for declaration in declarations}
    listed = [
        types.Tool(
            name=declaration.name,
            description=tool_description(declaration),
            inputSchema=declaration.input_schema,
        )
        for declaration in declared.values()
    ]
    server = Server(name, version=version)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return listed

    @server.call_tool(validate_input=False)
    async def call_tool(tool_name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        declaration = declared.get(tool_name)
        if declaration is None:
            raise ValueError(f"Tool '{tool_name}' not found")
        if isinstance(declaration, UnscopedToolDeclaration):
            outcome = await invoke_unscoped_declaration(declaration, arguments, caller, services)
        else:
            outcome = await invoke_declaration(declaration, arguments, scope, caller, services)
        return embedded_result(declaration, outcome)

    return McpSdkServerConfig(type="sdk", name=name, instance=server)


__all__ = ["embedded_result", "embedded_server"]
