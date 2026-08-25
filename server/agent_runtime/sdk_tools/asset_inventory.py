"""SDK MCP tool for committing an asset-inventory completion fact."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Mapping
from typing import Any

from claude_agent_sdk import tool
from pydantic import ValidationError

from lib.asset_inventory import (
    AssetInventoryError,
    AssetInventoryInvalidRequest,
    AssetInventoryRevisionConflict,
    AssetInventorySourceBlocked,
    complete_asset_inventory,
)
from lib.asset_types import (
    ASSET_SPECS,
    GLOBAL_ASSET_ID_FIELD,
    GLOBAL_ASSET_IMAGE_USAGE_FIELD,
    GLOBAL_ASSET_VOICE_SOURCE_FIELD,
    MATCHED_GLOBAL_ASSET_ID_FIELD,
    asset_name_comparison_key,
)
from lib.db import async_session_factory
from lib.db.repositories.asset_repo import AssetRepository
from lib.source_revision import SourceScope
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error
from server.services.character_voice_references import enqueue_character_voice_reference


def _json_response(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    response: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]
    }
    if is_error:
        response["is_error"] = True
    return response


async def _attach_exact_global_asset_matches(entries: object) -> object:
    """Attach one same-type, same-name match without asking the model to score candidates."""

    if not isinstance(entries, Mapping) or not any(isinstance(value, Mapping) and value for value in entries.values()):
        return entries
    async with async_session_factory() as session:
        assets = await AssetRepository(session).list(type=None, q=None, limit=10_000, offset=0)
    canonical_candidates: dict[tuple[str, str], set[str]] = {}
    alias_candidates: dict[tuple[str, str], set[str]] = {}
    assets_by_id = {asset.id: asset for asset in assets}
    for asset in assets:
        canonical_candidates.setdefault((asset.type, asset_name_comparison_key(asset.name)), set()).add(asset.id)
        for alias in asset.aliases:
            alias_candidates.setdefault((asset.type, alias.comparison_key), set()).add(asset.id)
    enriched = copy.deepcopy(entries)
    if not isinstance(enriched, dict):
        return entries
    for asset_type, spec in ASSET_SPECS.items():
        if not spec.in_global_library:
            continue
        bucket = enriched.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        for name, attrs in bucket.items():
            if not isinstance(name, str) or not isinstance(attrs, dict):
                continue
            # 匹配 ID 只能来自服务端全局库查询，忽略模型自行提交或提示注入伪造的 ID。
            for field in (
                MATCHED_GLOBAL_ASSET_ID_FIELD,
                GLOBAL_ASSET_ID_FIELD,
                GLOBAL_ASSET_IMAGE_USAGE_FIELD,
                GLOBAL_ASSET_VOICE_SOURCE_FIELD,
            ):
                attrs.pop(field, None)
            match_key = (asset_type, asset_name_comparison_key(name))
            canonical_ids = canonical_candidates.get(match_key)
            candidate_ids = canonical_ids if canonical_ids is not None else alias_candidates.get(match_key)
            matched_id = next(iter(candidate_ids)) if candidate_ids is not None and len(candidate_ids) == 1 else None
            if matched_id is not None:
                attrs[MATCHED_GLOBAL_ASSET_ID_FIELD] = matched_id
                attrs[GLOBAL_ASSET_ID_FIELD] = matched_id
                attrs[GLOBAL_ASSET_IMAGE_USAGE_FIELD] = "main"
                if asset_type == "character":
                    matched = assets_by_id[matched_id]
                    attrs[GLOBAL_ASSET_VOICE_SOURCE_FIELD] = (
                        "reference_audio" if matched.audio_path else "voice_id" if matched.voice_id else "none"
                    )
    return enriched


def complete_asset_inventory_tool(ctx: ToolContext):
    @tool(
        "complete_asset_inventory",
        "原子提交分析提取出的资产和资产清单事实。工具会在项目锁内重算 source revision；"
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
                "entries": {
                    "type": "object",
                    "description": "本次新增资产：{characters/scenes/props: {名称: {description, voice_style?}}}",
                },
            },
            "required": ["scope", "expected_source_revision"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            scope = SourceScope.model_validate(args.get("scope"))
            expected = args["expected_source_revision"]
            entries = await _attach_exact_global_asset_matches(args.get("entries"))
            completed = await asyncio.to_thread(
                complete_asset_inventory,
                ctx.pm,
                ctx.project_name,
                scope,
                expected,
                entries,
            )
            character_entries = entries.get("characters") if isinstance(entries, Mapping) else None
            character_names = (
                [name for name in character_entries if isinstance(name, str)]
                if isinstance(character_entries, Mapping)
                else []
            )

            async def _enqueue_voice(name: str) -> dict[str, Any]:
                try:
                    result = await enqueue_character_voice_reference(
                        ctx.project_name,
                        name,
                        strategy="video",
                        source="agent",
                        skip_existing_voice=True,
                        reuse_candidate=True,
                        manager=ctx.pm,
                        user_id=ctx.user_id,
                    )
                    return {"name": name, **result}
                except Exception as exc:  # voice availability must not roll back the committed inventory
                    return {"name": name, "status": "unavailable", "detail": str(exc)}

            # Inventory completion is the trigger point. These calls enqueue and
            # return immediately, so character-sheet generation can proceed while
            # the private speaking clips are still being produced.
            voice_references = await asyncio.gather(*(_enqueue_voice(name) for name in character_names))
            return _json_response(
                {
                    "scope": completed.scope.model_dump(mode="json"),
                    "source_revision": completed.source_revision,
                    "counts": completed.counts,
                    "voice_references": voice_references,
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
        except AssetInventoryInvalidRequest as exc:
            return _json_response({"error": "invalid_request", "detail": str(exc)}, is_error=True)
        except AssetInventoryError as exc:
            return _json_response({"error": "inventory_unavailable", "detail": str(exc)}, is_error=True)
        except (KeyError, ValidationError, ValueError) as exc:
            return _json_response({"error": "invalid_request", "detail": str(exc)}, is_error=True)
        except Exception as exc:  # noqa: BLE001
            return tool_error("complete_asset_inventory", exc)

    return _handler


__all__ = ["complete_asset_inventory_tool"]
