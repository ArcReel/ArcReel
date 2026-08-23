"""Agent adapters for the same MiniMax H3 prompt operation used by the Web UI."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from server.agent_runtime.sdk_tools._context import ToolContext, tool_error
from server.services.h3_prompt_optimization import H3PromptOptimizationService

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "episode": {"type": "integer", "minimum": 1},
        "unit_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "narration_delivery": {
            "type": "string",
            "enum": ["post_production", "use_tts"],
            "default": "post_production",
        },
        "confirmed_request_durations": {
            "type": "object",
            "additionalProperties": {"type": "integer", "minimum": 1},
        },
    },
    "required": ["episode"],
    "additionalProperties": False,
}


def _args(args: dict[str, Any]) -> dict[str, Any]:
    episode = args.get("episode")
    if not isinstance(episode, int) or isinstance(episode, bool) or episode < 1:
        raise ValueError("episode must be a positive integer")
    unit_ids = args.get("unit_ids")
    if unit_ids is not None and (
        not isinstance(unit_ids, list) or any(not isinstance(item, str) or not item.strip() for item in unit_ids)
    ):
        raise ValueError("unit_ids must be a list of non-empty strings")
    durations = args.get("confirmed_request_durations") or {}
    if not isinstance(durations, dict):
        raise ValueError("confirmed_request_durations must be an object")
    return {
        "episode": episode,
        "unit_ids": unit_ids,
        "narration_delivery": args.get("narration_delivery") or "post_production",
        "confirmed_request_durations": durations,
    }


def _response(key: str, values: list[Any]) -> dict[str, Any]:
    payload = {key: [value.model_dump(mode="json") for value in values]}
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], **payload}


def optimize_h3_video_prompts_tool(ctx: ToolContext):
    @tool(
        "optimize_h3_video_prompts",
        "基于最终 MiniMax H3 请求事实生成六段式视频提示词并保存为待审核；不会提交付费视频任务。",
        _SCHEMA,
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = _args(args)
            values = await H3PromptOptimizationService(ctx.pm).optimize(ctx.project_name, **request)
            return _response("artifacts", values)
        except Exception as exc:  # noqa: BLE001 - MCP adapters return a controlled error envelope
            return tool_error("optimize_h3_video_prompts", exc)

    return _handler


__all__ = ["optimize_h3_video_prompts_tool"]
