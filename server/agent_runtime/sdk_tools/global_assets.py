"""Read-only global asset context for the asset-analysis subagent."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from lib.db import async_session_factory
from lib.db.repositories.asset_repo import AssetRepository
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error


def list_global_assets_tool(ctx: ToolContext):
    @tool(
        "list_global_assets",
        "读取全局角色、场景和道具资产的精简上下文，供资产提取时复用已有名称。",
        {"type": "object", "properties": {}},
    )
    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        try:
            async with async_session_factory() as session:
                assets = await AssetRepository(session).list(type=None, q=None, limit=10_000, offset=0)
            grouped: dict[str, list[dict[str, Any]]] = {"characters": [], "scenes": [], "props": []}
            bucket_by_type = {"character": "characters", "scene": "scenes", "prop": "props"}
            for asset in assets:
                bucket = bucket_by_type.get(asset.type)
                if bucket is None:
                    continue
                grouped[bucket].append(
                    {
                        "name": asset.name,
                        "description": asset.description,
                        "aliases": [alias.alias for alias in asset.aliases],
                    }
                )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(grouped, ensure_ascii=False, sort_keys=True),
                    }
                ]
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("list_global_assets", exc)

    return _handler


__all__ = ["list_global_assets_tool"]
