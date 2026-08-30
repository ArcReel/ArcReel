"""SDK MCP adapter for the authoritative workflow-status service."""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import tool

from lib.script_review import complete_stale_script_plan_rebuild
from server.media_tools.context import ToolContext, tool_outcome_response, tool_services
from server.tool_runtime import (
    CompleteScriptPlanRebuildRequest,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    complete_script_plan_rebuild,
)


def complete_script_plan_rebuild_tool(ctx: ToolContext):
    @tool(
        "complete_script_plan_rebuild",
        "在 stale 分集脚本规划成功后原子记录完成事实；即使重建内容与旧 script_plan 相同，workflow-status 也能继续收敛。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "minimum": 1},
                "expected_stale_script_plan_revision": {"type": ["string", "null"]},
            },
            "required": ["episode", "expected_stale_script_plan_revision"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = CompleteScriptPlanRebuildRequest.model_validate(args)
        except ValueError as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        else:
            outcome = await complete_script_plan_rebuild(
                ToolRequest(request),
                ctx.scope,
                ctx.caller,
                tool_services(ctx),
                run_sync=asyncio.to_thread,
                complete=complete_stale_script_plan_rebuild,
            )
        return tool_outcome_response("script_plan_rebuild", outcome)

    return _handler


__all__ = ["complete_script_plan_rebuild_tool"]
