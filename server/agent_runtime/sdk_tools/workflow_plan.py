"""SDK MCP adapter for the authoritative workflow planner."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from lib.workflow_plan import WorkflowPlanRequest
from server.media_tools.context import ToolContext, tool_outcome_response, tool_services
from server.tool_runtime import ToolOutcome, ToolProblem, ToolRequest, get_workflow_plan


def get_workflow_plan_tool(ctx: ToolContext):
    @tool(
        "get_workflow_plan",
        "读取只读的完整工作流计划。返回有序步骤、阻断原因、活动任务、视频准入与唯一下一动作。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "minimum": 1},
                "narration_delivery": {
                    "type": "string",
                    "enum": ["post_production", "use_tts"],
                    "description": "本次视频请求的旁白交付选择；不写入项目 workflow。",
                },
                "confirmed_request_durations": {
                    "type": "object",
                    "additionalProperties": {"type": "integer", "minimum": 1},
                },
            },
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = WorkflowPlanRequest.model_validate(args)
        except ValueError as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        else:
            outcome = await get_workflow_plan(ToolRequest(request), ctx.scope, ctx.caller, tool_services(ctx))
        return tool_outcome_response("workflow_plan", outcome)

    return _handler


__all__ = ["get_workflow_plan_tool"]
