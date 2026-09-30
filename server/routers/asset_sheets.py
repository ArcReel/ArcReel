"""项目资产图的读取视图与批量生成入口。

- 状态：每张资产图（含衍生）按产物清单判定的现状与是否缺描述，供卡片状态、画廊筛选与批量按钮计数。
- 批量生成：画廊各类型页（类型范围）与集层下一步（集范围）共用一个服务命令
  （:mod:`server.services.admission.asset_sheet_batch`），与 Agent 的 ``generate_assets`` 同一份规划。
  预览只规划不建任务，供确认框列名单、跳过项与预估费用；提交不等待结果，返回成员任务供界面跟踪整批终态。
- 重生影响：单张重生一张过期资产图前，列出会随之过期的分镜图、视频与衍生资产图数量。
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Self

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lib.artifacts.artifact_activation import active_artifact_currency_resolver
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.generation.generation_queue import get_generation_queue
from lib.infra.api_errors import NotFoundError
from lib.project.asset_derivatives import split_derivative_artifact_id
from lib.project.project_manager import get_project_manager
from server.auth import CurrentUser
from server.services.admission.asset_sheet_batch import (
    DERIVATIVE_OWNER_SHEET_MISSING,
    DESCRIPTION_REQUIRED_CODES,
    AssetBatchEpisodeNotFound,
    AssetSheetBatchPlan,
    AssetSheetScope,
    asset_sheet_statuses,
    load_asset_sheet_batch_plan,
    submit_asset_sheet_batch,
)
from server.services.admission.cost_estimation import estimate_image_batch_cost
from server.services.currency.asset_regeneration_impact import asset_regeneration_impact

router = APIRouter()

AssetType = Literal["character", "scene", "prop", "product"]

#: 资产不存在时的文案 key；场景沿用历史前缀 ``project_scene_*``。
_NOT_FOUND_KEYS: dict[str, str] = {
    "character": "character_not_found",
    "scene": "project_scene_not_found",
    "prop": "prop_not_found",
    "product": "product_not_found",
}


class AssetSheetBatchRequest(BaseModel):
    """批量生成的范围：``asset_type``（画廊类型页）与 ``episode_id``（集层）二选一。"""

    model_config = ConfigDict(extra="forbid")

    asset_type: AssetType | None = None
    episode_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _one_scope(self) -> Self:
        if (self.asset_type is None) == (self.episode_id is None):
            raise ValueError("asset_type 与 episode_id 必须且只能给一个")
        return self

    def scope(self) -> AssetSheetScope:
        return AssetSheetScope(asset_type=self.asset_type, episode_id=self.episode_id)


def _unit_view(plan: AssetSheetBatchPlan, unit_id: str) -> dict[str, Any]:
    unit = plan.units.get(unit_id)
    if unit is None:
        asset_type, _, name = unit_id.partition("/")
        return {"unit_id": unit_id, "asset_type": asset_type, "name": name, "derivative": None}
    owner, derivative = split_derivative_artifact_id(unit.name) if unit.is_derivative else (unit.name, None)
    return {"unit_id": unit_id, "asset_type": unit.asset_type, "name": owner, "derivative": derivative}


def _skip_reason(code: str) -> str:
    if code in DESCRIPTION_REQUIRED_CODES:
        return "missing_description"
    if code == DERIVATIVE_OWNER_SHEET_MISSING:
        return "owner_sheet_missing"
    return "unavailable"


async def _plan(project_name: str, request: AssetSheetBatchRequest, user_id: str) -> tuple[dict, AssetSheetBatchPlan]:
    try:
        return await load_asset_sheet_batch_plan(
            get_project_manager(),
            get_generation_queue(),
            project_name=project_name,
            scope=request.scope(),
            user_id=user_id,
        )
    except AssetBatchEpisodeNotFound as exc:
        raise NotFoundError("episode_not_found", episode=exc.episode_id) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.get("/projects/{project_name}/asset-sheets/status")
async def get_asset_sheet_status(project_name: str):
    def _sync() -> list[dict[str, Any]]:
        manager = get_project_manager()
        try:
            project = manager.load_project(project_name)
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        resolver = active_artifact_currency_resolver(manager.get_project_path(project_name), project)
        return asset_sheet_statuses(project, resolver)

    return {"assets": await asyncio.to_thread(_sync)}


@router.post("/projects/{project_name}/asset-sheets/batch/preview")
async def preview_asset_sheet_batch(project_name: str, request: AssetSheetBatchRequest, user: CurrentUser):
    """规划一批但不建任务：要生成的名单、跳过项与能算出时的预估费用。"""

    project, plan = await _plan(project_name, request, user.id)
    new_ids = plan.new_task_ids
    skipped = [
        {**_unit_view(plan, unit_id), "reason": "generating"}
        for unit_id in plan.target_ids
        if unit_id in plan.generating
    ]
    skipped.extend(
        {**_unit_view(plan, item.unit_id), "reason": _skip_reason(item.problem.code if item.problem else "")}
        for item in plan.preflight.items
    )
    estimated = await estimate_image_batch_cost(
        project,
        ["i2i" if plan.units[unit_id].image_to_image else "t2i" for unit_id in new_ids],
        resolver=ConfigResolver(async_session_factory),
        session_factory=async_session_factory,
    )
    return {
        "targets": [{**_unit_view(plan, unit_id), "depends_on": plan.dependencies.get(unit_id)} for unit_id in new_ids],
        "skipped": skipped,
        "estimated_cost": estimated or None,
    }


@router.post("/projects/{project_name}/asset-sheets/batch")
async def submit_asset_sheet_batch_route(project_name: str, request: AssetSheetBatchRequest, user: CurrentUser):
    """提交一批，立即返回；成员任务的终态由界面按任务跟踪，整批汇总成一条通知。"""

    _project, plan = await _plan(project_name, request, user.id)
    batch, enqueued = await submit_asset_sheet_batch(
        plan,
        project_name=project_name,
        queue=get_generation_queue(),
        source="webui",
        user_id=user.id,
    )
    task_by_unit = {task.unit_id: task for task in enqueued if task.unit_id is not None}
    return {
        "batch_id": batch.batch_id,
        "members": [
            {
                **_unit_view(plan, member.unit_id),
                "task_id": task_by_unit[member.unit_id].task_id if member.unit_id in task_by_unit else None,
                "deduped": member.deduped,
                "status": member.status,
                "problem_code": member.problem.code if member.problem is not None else None,
            }
            for member in batch.members
        ],
    }


@router.get("/projects/{project_name}/asset-sheets/{asset_type}/{name}/regeneration-impact")
async def get_asset_regeneration_impact(
    project_name: str, asset_type: AssetType, name: str, derivative_name: str | None = None
):
    """重生这张资产图会让多少件现行产物转为过期。"""

    def _sync() -> dict[str, int]:
        try:
            impact = asset_regeneration_impact(
                get_project_manager(), project_name, asset_type, name, derivative_name=derivative_name
            )
        except KeyError as exc:
            raise NotFoundError(
                f"{'project_scene' if asset_type == 'scene' else asset_type}_not_found", name=name
            ) from exc
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        return {"storyboards": impact.storyboards, "videos": impact.videos, "derivatives": impact.derivatives}

    return await asyncio.to_thread(_sync)


__all__ = ["router"]
