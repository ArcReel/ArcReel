"""Host-neutral tools for asset image generation (character / scene / prop / product and derivatives).

Both tools are thin adapters over :mod:`server.services.admission.asset_sheet_batch`, the
service the Web gallery and episode entries call too, so an Agent listing or
generating by ``episode_id`` sees exactly what the Web episode entry sees.
``generate_assets`` addresses one asset by its qualified ``<type>/<name>`` unit ID
(a derivative is ``character/<owner>/<derivative>``): a single call may span four
asset types whose names can collide, and the per-ID result contract requires
globally unique IDs within one batch.
"""

from __future__ import annotations

import asyncio
from typing import Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from lib.artifacts.artifact_activation import active_artifact_currency_resolver
from lib.generation.generation_result import (
    GenerationResultBuilder,
    normalize_requested_ids,
    record_batch_outcomes,
)
from lib.project.asset_types import ASSET_SPECS
from server.media_tools.context import (
    GenerationToolValue,
    RequestedIds,
    generation_batch_submission_outcome,
    generation_result_outcome,
    tool_error,
    tool_problem,
)
from server.services.admission.asset_sheet_batch import (
    ASSET_BATCH_OPERATION,
    AssetBatchEpisodeNotFound,
    AssetSheetScope,
    load_asset_sheet_batch_plan,
)
from server.tool_runtime import (
    CallerContext,
    ProjectScope,
    Services,
    ToolOutcome,
    ToolRequest,
    migration_gate,
    submit_media_generation,
)

# Asset-type emoji shown in tool output. Other display fields (bucket_key,
# label_zh, subdir) come from lib.project.asset_types.ASSET_SPECS — the cross-app
# source of truth.
_EMOJI: dict[str, str] = {"character": "🧑", "scene": "🏠", "prop": "📦", "product": "🛍️"}

ALL_TYPES: tuple[str, ...] = tuple(ASSET_SPECS.keys())

AssetType = Literal["character", "scene", "prop", "product"]
if get_args(AssetType) != ALL_TYPES:
    raise RuntimeError("AssetType 须与 ASSET_SPECS 的资产类型逐项一致")

_EPISODE_ID_DESCRIPTION = (
    "集 ID；给出时只覆盖这一集正式脚本引用的资产（含商品与衍生，衍生连带其本体），与 Web 集层的批量生成同一范围。"
    "不能与 type / names 同时使用"
)


class ListPendingAssetsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: AssetType | SkipJsonSchema[None] = Field(default=None, description="资产类型；省略则汇总全部类型的待生成资产")
    episode_id: int | SkipJsonSchema[None] = Field(default=None, ge=1, description=_EPISODE_ID_DESCRIPTION)

    @model_validator(mode="after")
    def _episode_excludes_type(self) -> Self:
        if self.episode_id is not None and self.type is not None:
            raise ValueError("episode_id 不能与 type 同时使用")
        return self


def _preview(description: str | None) -> str:
    if description is None:
        return "（缺描述，补上前不会生成）"
    return description[:60] + "..." if len(description) > 60 else description


async def list_pending_assets(
    request: ToolRequest[ListPendingAssetsRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[str]:
    try:
        problem = await migration_gate(scope, services)
        if problem is not None:
            return ToolOutcome(problem=problem)
        value = request.value
        _project, plan = await load_asset_sheet_batch_plan(
            services.projects,
            services.queue,
            project_name=scope.project_name,
            scope=AssetSheetScope(asset_type=value.type, episode_id=value.episode_id),
            user_id=caller.user_id,
        )
        pending = [*plan.target_ids, *(item.unit_id for item in plan.preflight.items)]
        where = f"第 {value.episode_id} 集（id={value.episode_id}）引用的" if value.episode_id is not None else ""
        types = (value.type,) if value.type else ALL_TYPES
        lines: list[str] = []
        for asset_type in types:
            label = ASSET_SPECS[asset_type].label_zh
            of_type = [unit_id for unit_id in pending if plan.units[unit_id].asset_type == asset_type]
            if not of_type:
                lines.append(f"✅ 项目 '{scope.project_name}' {where}{label}都已有资产图")
                continue
            lines.append(f"\n📋 {where}待生成的{label} ({len(of_type)} 个):")
            for unit_id in of_type:
                unit = plan.units[unit_id]
                kind = f"（{unit.owner} 的衍生）" if unit.is_derivative else ""
                generating = "（生成中）" if unit_id in plan.generating else ""
                lines.append(f"  {_EMOJI[asset_type]} {unit.name}{kind}{generating} — {_preview(unit.description)}")
        if not value.type and not pending:
            lines.append(f"\n✅ 项目 '{scope.project_name}' {where}资产均已有资产图")
        return ToolOutcome(value="\n".join(lines))
    except AssetBatchEpisodeNotFound as exc:
        return tool_problem(str(exc), code="episode_not_found", params={"episode_id": exc.episode_id})
    except Exception as exc:
        return tool_error("list_pending_assets", exc)


class GenerateAssetsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: AssetType | SkipJsonSchema[None] = Field(
        default=None, description="资产类型；省略则按 character→scene→prop→product 顺序覆盖全部类型"
    )
    names: RequestedIds | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "要生成的资产名称列表，衍生写作 本体/衍生；必须配合 type 使用，与 all、episode_id 互斥。"
            "省略则只选缺资产图的资产"
        ),
    )
    all: bool = Field(
        default=False,
        description="显式要求扫描所选类型的全部缺图资产；与 names 互斥。省略 names 时本就只选缺图资产",
    )
    episode_id: int | SkipJsonSchema[None] = Field(default=None, ge=1, description=_EPISODE_ID_DESCRIPTION)

    @model_validator(mode="after")
    def _names_need_one_type(self) -> Self:
        if self.names is not None and self.type is None:
            raise ValueError("names 必须配合 type 使用")
        if self.names is not None and self.all:
            raise ValueError("all 与 names 互斥，不能同时使用")
        if self.episode_id is not None and (self.type is not None or self.names is not None):
            raise ValueError("episode_id 不能与 type / names 同时使用")
        return self


async def generate_assets(
    request: ToolRequest[GenerateAssetsRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[GenerationToolValue]:
    try:
        value = request.value
        names = normalize_requested_ids(value.names, field="names")
        project, plan = await load_asset_sheet_batch_plan(
            services.projects,
            services.queue,
            project_name=scope.project_name,
            scope=AssetSheetScope(
                asset_type=value.type,
                episode_id=value.episode_id,
                names=tuple(names) if names is not None else None,
            ),
            user_id=caller.user_id,
        )
        submitted = await submit_media_generation(
            scope=scope,
            caller=caller,
            services=services,
            operation=ASSET_BATCH_OPERATION,
            preflight=plan.preflight,
            pending_ids=plan.target_ids,
            specs=plan.task_specs(source=caller.source),
            states=dict(plan.states),
            dependencies=plan.dependencies,
        )
        if submitted.successes is None or submitted.failures is None:
            return generation_batch_submission_outcome(submitted.batch)

        builder = GenerationResultBuilder.from_preflight(plan.preflight)
        resolver = await asyncio.to_thread(
            active_artifact_currency_resolver, services.projects.get_project_path(scope.project_name), project
        )
        record_batch_outcomes(
            builder,
            successes=submitted.successes,
            failures=submitted.failures,
            states=plan.states,
            resolver=resolver,
            fallback_path=lambda unit_id: plan.units[unit_id].default_sheet_path,
            dependencies=plan.dependencies,
        )
        return generation_result_outcome(builder.build(), batch_id=submitted.batch.batch_id)
    except AssetBatchEpisodeNotFound as exc:
        return tool_problem(str(exc), code="episode_not_found", params={"episode_id": exc.episode_id})
    except Exception as exc:
        return tool_error(ASSET_BATCH_OPERATION, exc)


__all__ = [
    "ALL_TYPES",
    "GenerateAssetsRequest",
    "ListPendingAssetsRequest",
    "generate_assets",
    "list_pending_assets",
]
