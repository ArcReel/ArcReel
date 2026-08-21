"""Agent operations for linking project assets to the global library."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from server.agent_runtime.sdk_tools._context import ToolContext, tool_error
from server.services.project_asset_links import (
    ProjectAssetLinkError,
    ProjectAssetLinkNotFound,
    configure_project_asset_link,
    link_project_asset,
    unlink_project_asset,
)


def manage_project_asset_link_tool(ctx: ToolContext):
    @tool(
        "manage_project_asset_link",
        "链接、解除链接或配置项目角色/场景/道具与全局资产的关系；图片可作主图或生成参考图，角色可选择参考音频或 TTS Voice ID。",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["link", "unlink", "configure"]},
                "resource_type": {"type": "string", "enum": ["character", "scene", "prop"]},
                "resource_id": {"type": "string"},
                "asset_id": {"type": "string"},
                "image_usage": {"type": "string", "enum": ["main", "reference"]},
                "voice_source": {"type": "string", "enum": ["reference_audio", "voice_id", "none"]},
            },
            "required": ["action", "resource_type", "resource_id"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            action = args["action"]
            resource_type = args["resource_type"]
            resource_id = args["resource_id"]
            if action == "link":
                asset_id = args.get("asset_id")
                if not isinstance(asset_id, str) or not asset_id:
                    raise ProjectAssetLinkError("asset_id is required for link")
                entry, asset = await link_project_asset(
                    ctx.project_name, resource_type, resource_id, asset_id, manager=ctx.pm, source="worker"
                )
                payload = {"action": action, "project_asset": entry, "global_asset_id": asset.id}
            elif action == "unlink":
                entry = await unlink_project_asset(
                    ctx.project_name, resource_type, resource_id, manager=ctx.pm, source="worker"
                )
                payload = {"action": action, "project_asset": entry}
            elif action == "configure":
                entry, asset = await configure_project_asset_link(
                    ctx.project_name,
                    resource_type,
                    resource_id,
                    image_usage=args.get("image_usage"),
                    voice_source=args.get("voice_source"),
                    manager=ctx.pm,
                    source="worker",
                )
                payload = {"action": action, "project_asset": entry, "global_asset_id": asset.id}
            else:
                raise ProjectAssetLinkError("unsupported action")
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]}
        except (ProjectAssetLinkError, ProjectAssetLinkNotFound, FileNotFoundError, KeyError) as exc:
            return {
                "content": [{"type": "text", "text": json.dumps({"error": "asset_link_failed", "detail": str(exc)}, ensure_ascii=False)}],
                "is_error": True,
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("manage_project_asset_link", exc)

    return _handler


__all__ = ["manage_project_asset_link_tool"]
