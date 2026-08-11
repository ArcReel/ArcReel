"""SDK MCP adapter for the authoritative workflow-status service."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from lib.workflow_state import WorkflowStateService
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error


def _error(code: str, detail: str) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"error": code, "detail": detail}, ensure_ascii=False, sort_keys=True),
            }
        ],
        "is_error": True,
    }


def get_workflow_status_tool(ctx: ToolContext):
    @tool(
        "get_workflow_status",
        "读取服务端权威工作流状态、blocker、gate、产物事实和唯一 next_action。无副作用；不要再根据文件名自行推断阶段。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "minimum": 1},
                "include_details": {"type": "boolean", "default": True},
            },
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        raw_episode = args.get("episode")
        if raw_episode is not None and (
            not isinstance(raw_episode, int) or isinstance(raw_episode, bool) or raw_episode < 1
        ):
            return _error("invalid_episode", "episode must be a positive integer")
        include_details = args.get("include_details", True)
        if not isinstance(include_details, bool):
            return _error("invalid_request", "include_details must be a boolean")
        try:
            status = WorkflowStateService(ctx.pm).get_status(ctx.project_name, raw_episode)
            return {"content": [{"type": "text", "text": status.model_dump_json()}]}
        except ValueError as exc:
            return _error("invalid_episode", str(exc))
        except Exception as exc:  # noqa: BLE001
            return tool_error("get_workflow_status", exc)

    return _handler


__all__ = ["get_workflow_status_tool"]
