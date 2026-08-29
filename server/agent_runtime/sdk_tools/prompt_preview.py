"""SDK MCP adapter for the item prompt preview."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from server.media_tools.context import ToolContext, tool_outcome_response, tool_services
from server.tool_runtime import (
    PromptPreviewRequest,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    get_prompt_preview,
)


def get_prompt_preview_tool(ctx: ToolContext):
    @tool(
        "get_prompt_preview",
        "读取一个分镜条目最终会送进图像 / 视频模型的提示词文本。与执行路径同一渲染出口，不向供应商发请求、不产生费用。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名，如 episode_1.json"},
                "item_id": {"type": "string", "description": "分镜条目 id，如 E1S01"},
            },
            "required": ["script", "item_id"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = PromptPreviewRequest.model_validate(args)
        except ValueError as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        else:
            outcome = await get_prompt_preview(ToolRequest(request), ctx.scope, ctx.caller, tool_services(ctx))
        return tool_outcome_response("prompt_preview", outcome)

    return _handler


__all__ = ["get_prompt_preview_tool"]
