"""Host-neutral tools for asset image generation (character / scene / prop / product).

``generate_assets`` addresses one asset by its qualified ``<type>/<name>`` unit
ID: a single call may span four asset types whose names can collide, and the
per-ID result contract requires globally unique IDs within one batch.
"""

from __future__ import annotations

from typing import Any

from lib.artifact_activation import ArtifactCurrencyResolver, active_artifact_currency_resolver
from lib.artifact_manifest import ArtifactKey
from lib.asset_types import ASSET_SPECS, AssetSpec, asset_name_comparison_key, resolve_asset_key
from lib.generation_queue_client import (
    TaskSpec,
    batch_enqueue_and_wait,
)
from lib.generation_result import (
    GenerationAction,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationResultBuilder,
    GenerationSelectionMode,
    GenerationTargetState,
    normalize_requested_ids,
    record_batch_outcomes,
    select_generation_targets,
)
from lib.project_manager import ProjectManager
from server.media_tools.context import (
    ToolContext,
    generation_batch_submission_response,
    generation_result_response,
    migration_failure_for,
    migration_refusal_response,
    tool_error,
    tool_services,
)
from server.media_tools.definition import tool
from server.tool_runtime import submit_media_generation

# Asset-type emoji shown in tool output. Other display fields (bucket_key,
# label_zh, subdir) come from lib.asset_types.ASSET_SPECS — the cross-app
# source of truth.
_EMOJI: dict[str, str] = {"character": "🧑", "scene": "🏠", "prop": "📦", "product": "🛍️"}

ALL_TYPES: tuple[str, ...] = tuple(ASSET_SPECS.keys())

_OPERATION = "generate_assets"

_PENDING_DISPATCH = {
    "character": lambda pm, name: pm.get_pending_characters(name),
    "scene": lambda pm, name: pm.get_pending_project_scenes(name),
    "prop": lambda pm, name: pm.get_pending_project_props(name),
    "product": lambda pm, name: pm.get_pending_project_products(name),
}


def _get_pending(pm: ProjectManager, project_name: str, asset_type: str) -> list[dict]:
    return _PENDING_DISPATCH[asset_type](pm, project_name)


def asset_unit_id(asset_type: str, name: str) -> str:
    """Qualify an asset name so IDs stay unique across asset types."""

    return f"{asset_type}/{name}"


def asset_name_of(unit_id: str) -> str:
    """The bare asset name inside a qualified unit ID — inverse of :func:`asset_unit_id`."""

    return unit_id.split("/", 1)[1]


def _asset_candidates(
    project: dict[str, Any],
    asset_type: str,
    resolver: ArtifactCurrencyResolver,
) -> list[GenerationCandidate]:
    """Missing-only candidates for one asset type."""

    spec = ASSET_SPECS[asset_type]
    candidates: list[GenerationCandidate] = []
    for name, entry in (project.get(spec.bucket_key) or {}).items():
        sheet = entry.get(spec.sheet_field) if isinstance(entry, dict) else None
        candidates.append(
            GenerationCandidate(
                unit_id=asset_unit_id(asset_type, name),
                artifact_key=ArtifactKey.asset_sheet(asset_type, asset_name_comparison_key(name)),
                artifact_path=sheet if isinstance(sheet, str) else None,
            )
        )
    return candidates


def _requested_unit_ids(
    project: dict[str, Any],
    asset_type: str,
    names: list[str] | None,
) -> list[str] | None:
    """Resolve caller-supplied names to canonical qualified unit IDs.

    Unresolvable names keep their literal spelling so the selection reports them
    as unmatched instead of silently dropping them.
    """

    if names is None:
        return None
    assets_dict = (project.get(ASSET_SPECS[asset_type].bucket_key) or {}) if project else {}
    resolved: list[str] = []
    for name in names:
        key = resolve_asset_key(assets_dict, name)
        resolved.append(asset_unit_id(asset_type, key if key is not None else name))
    return list(dict.fromkeys(resolved))


def _description_of(project: dict[str, Any], asset_type: str, unit_id: str) -> str | None:
    spec = ASSET_SPECS[asset_type]
    name = asset_name_of(unit_id)
    entry = (project.get(spec.bucket_key) or {}).get(name)
    description = entry.get("description") if isinstance(entry, dict) else None
    return description if isinstance(description, str) and description.strip() else None


async def handle_list_pending_assets(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        failure = await migration_failure_for(ctx)
        if failure is not None:
            return migration_refusal_response(
                failure,
                text="❌ 项目数据升级未完成，产物清单不可读，无法列出待生成资产。请按明细修复后调用 retry_project_migration：",
            )
        asset_type = args.get("type")
        types = (asset_type,) if asset_type else ALL_TYPES
        lines: list[str] = []
        total = 0
        for t in types:
            spec = ASSET_SPECS[t]
            pending = _get_pending(ctx.pm, ctx.project_name, t)
            if not pending:
                lines.append(f"✅ 项目 '{ctx.project_name}' 所有{spec.label_zh}都已有资产图")
                continue
            total += len(pending)
            lines.append(f"\n📋 待生成的{spec.label_zh} ({len(pending)} 个):")
            for item in pending:
                desc = item.get("description", "") or ""
                desc_preview = desc[:60] + "..." if len(desc) > 60 else desc
                lines.append(f"  {_EMOJI[t]} {item['name']} — {desc_preview}")
        if not asset_type and total == 0:
            lines.append(f"\n✅ 项目 '{ctx.project_name}' 所有资产均已有资产图")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}
    except Exception as exc:  # noqa: BLE001
        return tool_error("list_pending_assets", exc)


def list_pending_assets_tool(ctx: ToolContext):
    @tool(
        "list_pending_assets",
        "列出项目内待生成资产图的角色/场景/道具/商品。type 省略则汇总所有类型。",
        {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": list(ALL_TYPES),
                    "description": "资产类型；不传则列出所有类型的 pending",
                },
            },
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        return await handle_list_pending_assets(ctx, args)

    return _handler


async def handle_generate_assets(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        asset_type = args.get("type")
        names = normalize_requested_ids(args.get("names"), field="names")
        all_flag = bool(args.get("all"))
        if names is not None and not asset_type:
            return {
                "content": [{"type": "text", "text": "names 必须配合 type 使用"}],
                "is_error": True,
            }
        if names is not None and all_flag:
            return {
                "content": [{"type": "text", "text": "all 与 names 互斥，不能同时使用"}],
                "is_error": True,
            }

        project = ctx.pm.load_project(ctx.project_name)
        resolver = active_artifact_currency_resolver(ctx.project_path, project)
        types = (asset_type,) if asset_type else ALL_TYPES

        builder = GenerationResultBuilder(
            _OPERATION,
            GenerationSelectionMode.EXPLICIT if names is not None else GenerationSelectionMode.MISSING_ONLY,
        )
        targets: list[tuple[AssetSpec, GenerationTargetState]] = []
        for t in types:
            spec = ASSET_SPECS[t]
            selection = select_generation_targets(
                candidates=_asset_candidates(project, t, resolver),
                requested_ids=_requested_unit_ids(project, t, names),
                resolver=resolver,
            )
            builder.absorb(selection)
            for state in selection.targets:
                if _description_of(project, t, state.unit_id) is None:
                    builder.block(
                        state.unit_id,
                        problem=GenerationProblem(
                            code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                            detail=f"{spec.label_zh} '{state.unit_id}' 缺少 description，无法生成资产图",
                            action=GenerationAction.FIX_INPUT,
                        ),
                        artifact_key=state.artifact_key,
                        artifact_path=state.artifact_path,
                        artifact_status=state.status,
                    )
                    continue
                targets.append((spec, state))

        by_id = {state.unit_id: state for _spec, state in targets}

        task_specs = [
            TaskSpec.from_request(
                task_type=spec.asset_type,
                media_type="image",
                resource_id=asset_name_of(state.unit_id),
                unit_id=state.unit_id,
                prompt=_description_of(project, spec.asset_type, state.unit_id),
                source=ctx.caller.source,
            )
            for spec, state in targets
        ]
        submitted = await submit_media_generation(
            scope=ctx.scope,
            caller=ctx.caller,
            services=tool_services(ctx),
            operation=_OPERATION,
            preflight=builder.build(),
            pending_ids=[state.unit_id for _spec, state in targets],
            specs=task_specs,
            states=by_id,
            embedded_waiter=batch_enqueue_and_wait,
        )
        if submitted.successes is None or submitted.failures is None:
            return generation_batch_submission_response(submitted.batch)
        record_batch_outcomes(
            builder,
            successes=submitted.successes,
            failures=submitted.failures,
            states=by_id,
            resolver=resolver,
            unit_id_of=lambda rid: (
                rid if "/" in rid else next(unit_id for unit_id in by_id if asset_name_of(unit_id) == rid)
            ),
            fallback_path=lambda unit_id: (
                f"{ASSET_SPECS[unit_id.split('/', 1)[0]].subdir}/{asset_name_of(unit_id)}.png"
            ),
        )

        return generation_result_response(builder.build(), batch_id=submitted.batch.batch_id)
    except Exception as exc:  # noqa: BLE001
        return tool_error(_OPERATION, exc)


def generate_assets_tool(ctx: ToolContext):
    @tool(
        _OPERATION,
        "批量生成角色/场景/道具/商品资产图。"
        "type 省略则按 character→scene→prop→product 顺序纳入同一 durable batch；"
        "names 指定具体名称（必须同时给 type）；all=true 表示该 type 的全部缺图资产。"
        "不传 names 时只选缺资产图的资产：已失效但可用的旧图会被复用，不会自动重生。"
        "结果按 requested / succeeded / failed / blocked 逐 ID 返回，ID 形如 character/张三。",
        {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": list(ALL_TYPES),
                    "description": "资产类型；不传等于全部类型",
                },
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "目标资产名称列表；必须配合 type 使用",
                },
                "all": {
                    "type": "boolean",
                    "description": "是否扫描所有缺图资产（与 names 互斥；默认 false 但当未提供 names 时等同 true）",
                },
            },
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        return await handle_generate_assets(ctx, args)

    return _handler


__all__ = [
    "ALL_TYPES",
    "asset_name_of",
    "asset_unit_id",
    "list_pending_assets_tool",
    "generate_assets_tool",
]
