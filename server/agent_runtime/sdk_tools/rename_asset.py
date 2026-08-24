"""SDK MCP tool：资产级联重命名（character / scene / prop / product）。

独立于 ``patch_project``：后者的章程限定 project.json 字段写入，而重命名是跨全部剧集
剧本 + step1 草稿 + 文件系统的级联事务（见 docs/adr/0057），经
``ProjectManager.rename_asset`` 在锁内一次完成——资产桶 key、剧本引用（引用数组 /
speaker / ``@[名称]`` mention）、按名命名的关联文件与版本历史同步改齐。目标名冲突
（NFC 归一判定）时整体拒绝、不落盘。
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from server.agent_runtime.sdk_tools._context import ToolContext, tool_outcome_response, tool_services
from server.tool_runtime import (
    ASSET_TABLES as _TABLES,
)
from server.tool_runtime import (
    RenameAssetRequest,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    rename_asset,
)


def rename_asset_tool(ctx: ToolContext):
    @tool(
        "rename_asset",
        "级联重命名资产:一次改齐资产表 key、全部剧集剧本与 step1 草稿的名称引用"
        "(引用数组 / speaker / @[名称] mention)以及按名命名的关联文件与版本历史。"
        "新名与既有同类型资产冲突时整体拒绝、不落盘。改名不影响全局资产库。"
        "用 patch_project 改名等于「新名新建 + 旧名残留」,改名一律用本工具。",
        {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "enum": list(_TABLES),
                    "description": "资产表:characters / scenes / props / products",
                },
                "old_name": {"type": "string", "description": "现有资产名"},
                "new_name": {"type": "string", "description": "新资产名"},
            },
            "required": ["table", "old_name", "new_name"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = RenameAssetRequest.model_validate(args)
        except ValueError as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        else:
            outcome = await rename_asset(ToolRequest(request), ctx.scope, ctx.caller, tool_services(ctx))
        return tool_outcome_response("asset_rename", outcome)

    return _handler


__all__ = ["rename_asset_tool"]
