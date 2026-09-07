"""SDK MCP adapter for converting a script plan into the formal script."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from server.media_tools.context import ToolContext, tool_outcome_response, tool_services
from server.tool_runtime import (
    ScriptPlanConversionRequest,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    convert_script_plan,
)


def convert_script_plan_tool(ctx: ToolContext):
    @tool(
        "convert_script_plan",
        (
            "按脚本规划机械转为正式脚本，不调用文本模型：只同步内容层，分镜的 image_prompt / video_prompt "
            "以待生成（null）落盘，之后用 generate_episode_script 传 entry_ids 补写。已有正式脚本时只新增 / 移出 / "
            "改序，失效分镜原样保留；传 entry_ids 让指定的失效分镜「采用新内容」（内容按脚本规划重取、提示词保留）。"
            "须先完成内容确认。回执列出 added / refreshed / removed 三组条目 id。"
        ),
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "minimum": 1, "description": "剧集编号"},
                "entry_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要「采用新内容」的失效分镜 id 列表（可选）；不传即只做集合同步",
                },
            },
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = ScriptPlanConversionRequest.model_validate(args)
        except ValueError as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        else:
            outcome = await convert_script_plan(ToolRequest(request), ctx.scope, ctx.caller, tool_services(ctx))
        return tool_outcome_response("script_plan_conversion", outcome)

    return _handler


__all__ = ["convert_script_plan_tool"]
