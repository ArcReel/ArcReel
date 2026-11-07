"""Project asset-image read views and batch generation.

- State: artifact-manifest status and missing-description flags for every image, including derivatives, support card states,
  gallery filters and batch-button counts. Unreadable manifests report blocked for each image.
- Batch generation: gallery type pages (type scope) and episode next-step actions (episode scope) share one service command
  (:mod:`server.services.admission.asset_sheet_batch`) and the Agent’s ``generate_assets`` plan. Preview only plans without
  tasks, supplying confirmation targets, skips and estimated costs. Submission returns member tasks without awaiting results,
  allowing the UI to track the whole batch’s terminal state.
- Regeneration impact: before individually regenerating a stale asset image, count storyboard images, videos and derivative
  images that would become stale."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, Self

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lib.artifacts.artifact_activation import active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactManifestError
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
from server.services.tasks.formal_image_commit import get_aspect_ratio

logger = logging.getLogger(__name__)

router = APIRouter()

AssetType = Literal["character", "scene", "prop", "product"]

#: Message key for missing assets; scenes retain the historical ``project_scene_*`` prefix.
_NOT_FOUND_KEYS: dict[str, str] = {
    "character": "character_not_found",
    "scene": "project_scene_not_found",
    "prop": "prop_not_found",
    "product": "product_not_found",
}


class AssetSheetBatchRequest(BaseModel):
    """Batch scope: choose exactly one of ``asset_type`` (gallery type page) and ``episode_id`` (episode level)."""

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
        try:
            resolver = active_artifact_currency_resolver(manager.get_project_path(project_name), project)
        except ArtifactManifestError:
            logger.exception("asset sheet status: artifact manifest unreadable for project %s", project_name)
            resolver = None
        return asset_sheet_statuses(project, resolver)

    return {"assets": await asyncio.to_thread(_sync)}


@router.post("/projects/{project_name}/asset-sheets/batch/preview")
async def preview_asset_sheet_batch(project_name: str, request: AssetSheetBatchRequest, user: CurrentUser):
    """Plan a batch without tasks: generation targets, skipped items, estimated costs of priced items and the count of unpriced items."""

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
        aspect_ratio=get_aspect_ratio(project, "characters"),
        resolver=ConfigResolver(async_session_factory),
        session_factory=async_session_factory,
    )
    return {
        "targets": [{**_unit_view(plan, unit_id), "depends_on": plan.dependencies.get(unit_id)} for unit_id in new_ids],
        "skipped": skipped,
        **estimated.to_payload(),
    }


@router.post("/projects/{project_name}/asset-sheets/batch")
async def submit_asset_sheet_batch_route(project_name: str, request: AssetSheetBatchRequest, user: CurrentUser):
    """Submit a batch and return immediately; the UI tracks member task terminal states and consolidates the batch into one notification."""

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
    """Whether this asset image is stale now and how many current artifacts regeneration would make stale."""

    def _sync() -> dict[str, bool | int]:
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
        return {
            "stale": impact.stale,
            "storyboards": impact.storyboards,
            "videos": impact.videos,
            "derivatives": impact.derivatives,
        }

    return await asyncio.to_thread(_sync)


__all__ = ["router"]
