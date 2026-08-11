"""SDK MCP tool for committing an asset-inventory completion fact."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool
from pydantic import ValidationError

from lib.asset_inventory import (
    AssetInventoryRevisionConflict,
    AssetInventorySourceBlocked,
    complete_asset_inventory,
)
from lib.source_revision import SourceScope
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error


def _json_response(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    response: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]
    }
    if is_error:
        response["is_error"] = True
    return response


def complete_asset_inventory_tool(ctx: ToolContext):
    @tool(
        "complete_asset_inventory",
        "在分析完成后记录资产清单事实。工具会在项目锁内重算 source revision；"
        "与 expected_source_revision 不一致时整笔拒绝，不修改 project.json。空角色/场景/道具清单是合法结果。",
        {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["all", "files"]},
                        "files": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["kind"],
                },
                "expected_source_revision": {"type": "string"},
            },
            "required": ["scope", "expected_source_revision"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            scope = SourceScope.model_validate(args.get("scope"))
            expected = args["expected_source_revision"]
            completed = complete_asset_inventory(ctx.pm, ctx.project_name, scope, expected)
            return _json_response(
                {
                    "scope": completed.scope.model_dump(mode="json"),
                    "source_revision": completed.source_revision,
                    "counts": completed.counts,
                }
            )
        except AssetInventoryRevisionConflict as exc:
            return _json_response(
                {
                    "error": "source_revision_conflict",
                    "expected_source_revision": exc.expected_revision,
                    "actual_source_revision": exc.actual_revision,
                },
                is_error=True,
            )
        except AssetInventorySourceBlocked as exc:
            return _json_response(
                {
                    "error": "source_blocked",
                    "blockers": [blocker.model_dump(mode="json") for blocker in exc.blockers],
                },
                is_error=True,
            )
        except (KeyError, ValidationError, ValueError) as exc:
            return _json_response({"error": "invalid_request", "detail": str(exc)}, is_error=True)
        except Exception as exc:  # noqa: BLE001
            return tool_error("complete_asset_inventory", exc)

    return _handler


__all__ = ["complete_asset_inventory_tool"]
