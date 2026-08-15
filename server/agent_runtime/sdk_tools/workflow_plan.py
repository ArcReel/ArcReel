"""SDK MCP adapter for the authoritative workflow planner."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from lib.workflow_plan import WorkflowPlanRequest
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error
from server.services import workflow_planner


def _error(code: str, detail: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps({"error": code, "detail": detail}, ensure_ascii=False)}],
        "is_error": True,
    }


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
            return _error("invalid_request", str(exc))
        try:
            plan = await workflow_planner.get_workflow_planner(ctx.pm).get_plan(ctx.project_name, request)
            return {"content": [{"type": "text", "text": plan.model_dump_json()}]}
        except json.JSONDecodeError as exc:
            return tool_error("get_workflow_plan", exc)
        except ValueError as exc:
            return _error("invalid_request", str(exc))
        except Exception as exc:  # noqa: BLE001
            return tool_error("get_workflow_plan", exc)

    return _handler


__all__ = ["get_workflow_plan_tool"]
