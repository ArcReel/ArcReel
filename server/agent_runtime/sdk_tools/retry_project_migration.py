"""SDK MCP adapter for rerunning a project's schema migration chain."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from server.agent_runtime.sdk_tools._context import ToolContext, tool_outcome_response, tool_services
from server.tool_runtime import ToolRequest, retry_project_migration


def retry_project_migration_tool(ctx: ToolContext):
    @tool(
        "retry_project_migration",
        "重跑本项目的数据升级链（含产物补录）。升级失败时项目被阻断，阻断期仍可用的写入工具只有"
        "patch_project / patch_episode_meta / rename_asset；patch_episode_script 一律被拒。"
        "按失败明细用前三个修好被点名的集 / 文件，再调用"
        "本工具；这三个工具改不到的位置（如剧本正文类违约）如实报告卡点给用户，不要反复重试。"
        "幂等：已是最新版本时直接返回成功。成功返回新的制作计划；失败返回结构化明细"
        "（episode / file / violation）。",
        {"type": "object", "properties": {}},
    )
    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        outcome = await retry_project_migration(ToolRequest(None), ctx.scope, ctx.caller, tool_services(ctx))
        return tool_outcome_response("migration_retry", outcome)

    return _handler


__all__ = ["retry_project_migration_tool"]
