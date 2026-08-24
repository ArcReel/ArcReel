"""Embedded MCP adapters for project entry tools."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from server.agent_runtime.sdk_tools._context import ToolContext, tool_outcome_response, tool_services
from server.tool_runtime import (
    CreateProjectToolRequest,
    ProjectScope,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    UploadSourceRequest,
    create_project,
    list_projects,
    upload_source,
)


def list_projects_tool(ctx: ToolContext):
    @tool("list_projects", "列出可供后续工具寻址的 ArcReel 项目。", {"type": "object", "properties": {}})
    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        outcome = await list_projects(ToolRequest(None), ctx.caller, tool_services(ctx))
        return tool_outcome_response("projects", outcome)

    return _handler


def create_project_tool(ctx: ToolContext):
    @tool(
        "create_project",
        "创建 ArcReel 项目并写入可供后续工具使用的完整项目元数据。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "项目唯一标识，只能包含字母、数字和连字符。"},
                "title": {"type": "string", "description": "用户可见标题。"},
                "content_mode": {"type": "string", "enum": ["narration", "drama", "ad"]},
                "source_kind": {"type": "string", "enum": ["novel", "screenplay"]},
                "generation_mode": {"type": "string", "enum": ["storyboard", "reference_video"]},
                "grid_storyboard": {"type": "boolean"},
                "aspect_ratio": {"type": "string"},
                "default_duration": {"type": "integer", "minimum": 1},
                "target_duration": {"type": "integer", "minimum": 1},
                "brief": {"type": "string"},
            },
            "required": ["name"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = CreateProjectToolRequest.model_validate(args)
        except ValueError as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        else:
            outcome = await create_project(ToolRequest(request), ctx.caller, tool_services(ctx))
        return tool_outcome_response("project", outcome)

    return _handler


def upload_source_tool(ctx: ToolContext):
    @tool(
        "upload_source",
        "把文本源文件规范化为 UTF-8 并写入当前或显式指定的项目，供源文读取与分集规划使用。",
        {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "目标项目；省略时使用当前会话项目。"},
                "filename": {"type": "string", "description": "带 .txt 或 .md 扩展名的文件名。"},
                "content": {"type": "string", "description": "源文件的文本内容。"},
                "on_conflict": {"type": "string", "enum": ["fail", "replace", "rename"]},
            },
            "required": ["filename", "content"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        payload = dict(args)
        project = payload.pop("project", ctx.project_name)
        try:
            project_name = ctx.pm.normalize_project_name(project)
            ctx.pm.get_project_path(project_name)
            if not ctx.pm.project_exists(project_name):
                raise FileNotFoundError(f"项目 '{project_name}' 缺少 project.json")
            request = UploadSourceRequest.model_validate(payload)
        except (FileNotFoundError, ValueError) as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        else:
            scope = ProjectScope(project_name=project_name, projects_root=ctx.projects_root)
            outcome = await upload_source(ToolRequest(request), scope, ctx.caller, tool_services(ctx))
        return tool_outcome_response("source", outcome)

    return _handler


__all__ = ["create_project_tool", "list_projects_tool", "upload_source_tool"]
