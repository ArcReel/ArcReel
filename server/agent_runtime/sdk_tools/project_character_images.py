"""Agent operations for project character image-slot transitions."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from server.agent_runtime.sdk_tools._context import ToolContext, tool_error
from server.services.project_character_images import (
    ProjectCharacterImageError,
    move_character_main_to_reference,
)


def move_character_main_to_reference_tool(ctx: ToolContext):
    @tool(
        "move_character_main_to_reference",
        "把角色卡片当前主图移入参考图槽并清空主图；主图可以来自已链接的全局资产或当前项目。",
        {
            "type": "object",
            "properties": {
                "character_name": {"type": "string"},
            },
            "required": ["character_name"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await move_character_main_to_reference(
                ctx.project_name,
                args["character_name"],
                manager=ctx.pm,
                source="worker",
            )
            payload = {
                "project_asset": result.project_asset,
                "source": result.source,
                "reference_path": result.reference_path,
            }
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]}
        except (ProjectCharacterImageError, FileNotFoundError, KeyError) as exc:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"error": "character_image_move_failed", "detail": str(exc)},
                            ensure_ascii=False,
                        ),
                    }
                ],
                "is_error": True,
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("move_character_main_to_reference", exc)

    return _handler


__all__ = ["move_character_main_to_reference_tool"]
