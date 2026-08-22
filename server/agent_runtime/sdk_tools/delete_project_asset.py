"""SDK MCP tool：删除项目内资产（character / scene / prop / product）。

Web DELETE 路由与本工具都只负责边界适配，实际删除统一收敛到
``ProjectManager.delete_asset``：在同一提交中移除 project.json 资产条目及其正式
资产图 claim。全局资产库与历史媒体文件不属于该操作的删除范围。
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from lib.asset_types import ASSET_SPECS
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error

_TABLES = tuple(spec.bucket_key for spec in ASSET_SPECS.values())


def delete_project_asset_tool(ctx: ToolContext):
    @tool(
        "delete_project_asset",
        "从当前项目删除一个角色、场景、道具或产品资产。仅在用户明确要求删除时调用。"
        "本操作删除 project.json 资产条目及其正式资产图声明，不删除全局资产库条目或历史媒体文件。",
        {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "enum": list(_TABLES),
                    "description": "资产表:characters / scenes / props / products",
                },
                "name": {"type": "string", "description": "要删除的现有资产名"},
            },
            "required": ["table", "name"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            table = str(args["table"])
            name = str(args["name"])
            ctx.pm.delete_asset(ctx.project_name, table, name)
            return {"content": [{"type": "text", "text": f"已从当前项目删除 {table} 资产 {name!r}。"}]}
        except Exception as exc:  # noqa: BLE001
            return tool_error("delete_project_asset", exc)

    return _handler


__all__ = ["delete_project_asset_tool"]
