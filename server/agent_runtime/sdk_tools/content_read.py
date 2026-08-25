"""Embedded-host adapters for the shared project content readers."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from server.media_tools.context import (
    ToolContext,
    migration_failure_for,
    migration_refusal_response,
    tool_outcome_response,
    tool_services,
)
from server.tool_runtime import (
    ToolRequest,
    get_episode_script,
    get_project_content,
    get_source_text,
    get_step1_content,
    list_project_files,
    list_source_files,
    read_project_file,
)


def get_project_content_tool(ctx: ToolContext):
    @tool("get_project_content", "读取项目创作内容与 canonical revision。", {"type": "object", "properties": {}})
    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        return tool_outcome_response(
            "project_content", await get_project_content(ToolRequest(None), ctx.scope, ctx.caller, tool_services(ctx))
        )

    return _handler


def list_source_files_tool(ctx: ToolContext):
    @tool("list_source_files", "列出项目源文文件及其 revision/etag。", {"type": "object", "properties": {}})
    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        return tool_outcome_response(
            "source_files", await list_source_files(ToolRequest(None), ctx.scope, ctx.caller, tool_services(ctx))
        )

    return _handler


def get_source_text_tool(ctx: ToolContext):
    @tool(
        "get_source_text",
        "读取 source/ 下 UTF-8 文本及其 revision。path 使用项目相对路径。",
        {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "如 source/episode_1.txt"}},
            "required": ["path"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        return tool_outcome_response(
            "source_text", await get_source_text(ToolRequest(args["path"]), ctx.scope, ctx.caller, tool_services(ctx))
        )

    return _handler


def get_episode_script_tool(ctx: ToolContext):
    @tool(
        "get_episode_script",
        "读取剧本正文与 canonical revision；把 revision 原样用于 patch_episode_script.base_revision。",
        {
            "type": "object",
            "properties": {"script": {"type": "string", "description": "剧本纯文件名，如 episode_1.json"}},
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        failure = await migration_failure_for(ctx)
        if failure is not None:
            return migration_refusal_response(
                failure,
                text="❌ 项目数据升级未完成，剧本编辑已全部关闭，revision 不再签发。"
                "请按明细修复后调用 retry_project_migration：",
            )
        return tool_outcome_response(
            "episode_script",
            await get_episode_script(ToolRequest(args["script"]), ctx.scope, ctx.caller, tool_services(ctx)),
        )

    return _handler


def get_step1_content_tool(ctx: ToolContext):
    @tool(
        "get_step1_content",
        "读取指定集当前正式 step1 正文与 canonical revision。",
        {
            "type": "object",
            "properties": {"episode": {"type": "integer", "minimum": 1}},
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        return tool_outcome_response(
            "step1_content",
            await get_step1_content(ToolRequest(args["episode"]), ctx.scope, ctx.caller, tool_services(ctx)),
        )

    return _handler


def list_project_files_tool(ctx: ToolContext):
    @tool(
        "list_project_files",
        "列出诊断可读的项目业务文本文件；敏感文件、symlink 与其他路径不开放。",
        {"type": "object", "properties": {}},
    )
    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        return tool_outcome_response(
            "project_files", await list_project_files(ToolRequest(None), ctx.scope, ctx.caller, tool_services(ctx))
        )

    return _handler


def read_project_file_tool(ctx: ToolContext):
    @tool(
        "read_project_file",
        "读取白名单内项目业务文本文件及其 revision/etag；优先使用专用 reader。",
        {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "项目相对路径"}},
            "required": ["path"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        return tool_outcome_response(
            "project_file",
            await read_project_file(ToolRequest(args["path"]), ctx.scope, ctx.caller, tool_services(ctx)),
        )

    return _handler


__all__ = [
    "get_episode_script_tool",
    "get_project_content_tool",
    "get_source_text_tool",
    "get_step1_content_tool",
    "list_project_files_tool",
    "list_source_files_tool",
    "read_project_file_tool",
]
