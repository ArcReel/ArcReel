"""SDK MCP tool for accepting user-reviewed existing asset sheets as current."""

from __future__ import annotations

from claude_agent_sdk import tool

from server.agent_runtime.sdk_tools._context import ToolContext, tool_error
from server.services.asset_sheet_reviews import AssetSheetSelection, confirm_asset_sheets_current


def confirm_asset_sheets_tool(ctx: ToolContext):
    @tool(
        "confirm_asset_sheets",
        "将用户已经明确审核通过的现有角色、场景、道具或商品素材图登记为匹配当前素材定义。"
        "只更新素材新鲜度声明，不生成、编辑、上传或删除图片，不改变版本历史。"
        "仅在用户明确确认现有素材图就是最新版本时调用；assets 省略时确认项目内全部现有素材图。",
        {
            "type": "object",
            "properties": {
                "assets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "asset_type": {
                                "type": "string",
                                "enum": ["character", "scene", "prop", "product"],
                            },
                            "name": {"type": "string"},
                        },
                        "required": ["asset_type", "name"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                }
            },
            "additionalProperties": False,
        },
    )
    async def _handler(args: dict):
        try:
            raw_assets = args.get("assets")
            selections = (
                None
                if raw_assets is None
                else [AssetSheetSelection(str(item["asset_type"]), str(item["name"])) for item in raw_assets]
            )
            result = confirm_asset_sheets_current(
                ctx.project_name,
                selections=selections,
                manager=ctx.pm,
            )
            names = "、".join(item["name"] for item in result["confirmed"])
            state = "已更新" if result["changed"] else "已是当前状态"
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"✅ {state}：已确认 {result['confirmed_count']} 张现有素材图为 current（{names}）。未调用生成模型。",
                    }
                ]
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("confirm_asset_sheets", exc)

    return _handler


__all__ = ["confirm_asset_sheets_tool"]
