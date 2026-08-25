"""SDK adapters for durable generation batch query and cancellation."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from server.agent_runtime.sdk_tools._context import ToolContext, tool_outcome_response, tool_services
from server.tool_runtime import (
    GenerationBatchToolRequest,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    cancel_generation_batch,
    get_generation_batch,
)

_SCHEMA = {
    "type": "object",
    "properties": {"batch_id": {"type": "string", "minLength": 1}},
    "required": ["batch_id"],
}


def _batch_tool(ctx: ToolContext, *, cancel: bool):
    name = "cancel_generation_batch" if cancel else "get_generation_batch"
    description = (
        "取消整个生成批次。queued 立即取消，running 进入 cancelling；重复调用安全。"
        if cancel
        else "查询生成批次的成员状态、计数、建议轮询间隔与终态 generation_result。"
    )

    @tool(name, description, _SCHEMA)
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = GenerationBatchToolRequest.model_validate(args)
        except ValueError as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        else:
            handler = cancel_generation_batch if cancel else get_generation_batch
            outcome = await handler(ToolRequest(request), ctx.scope, ctx.caller, tool_services(ctx))
        return tool_outcome_response("generation_batch_cancellation" if cancel else "generation_batch", outcome)

    return _handler


def get_generation_batch_tool(ctx: ToolContext):
    return _batch_tool(ctx, cancel=False)


def cancel_generation_batch_tool(ctx: ToolContext):
    return _batch_tool(ctx, cancel=True)


__all__ = ["cancel_generation_batch_tool", "get_generation_batch_tool"]
