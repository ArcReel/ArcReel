"""SDK MCP adapter for the authoritative workflow-status service."""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import tool

from lib.script_review import complete_stale_step1_rebuild
from server.agent_runtime.sdk_tools._context import ToolContext, tool_outcome_response, tool_services
from server.tool_runtime import (
    CompleteStep1RebuildRequest,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    complete_step1_rebuild,
)


def complete_step1_rebuild_tool(ctx: ToolContext):
    @tool(
        "complete_step1_rebuild",
        "在 stale 分集内容整理成功后原子记录完成事实；即使重建内容与旧 step1 相同，workflow-status 也能继续收敛。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "minimum": 1},
                "expected_stale_step1_revision": {"type": ["string", "null"]},
            },
            "required": ["episode", "expected_stale_step1_revision"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = CompleteStep1RebuildRequest.model_validate(args)
        except ValueError as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        else:
            outcome = await complete_step1_rebuild(
                ToolRequest(request),
                ctx.scope,
                ctx.caller,
                tool_services(ctx),
                run_sync=asyncio.to_thread,
                complete=complete_stale_step1_rebuild,
            )
        return tool_outcome_response("step1_rebuild", outcome)

    return _handler


__all__ = ["complete_step1_rebuild_tool"]
