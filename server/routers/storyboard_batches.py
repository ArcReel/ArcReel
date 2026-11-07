"""Batch generation of episode storyboard images and videos from timeline/grid toolbars.

- Images use the same plan as Agent ``generate_storyboards`` (:mod:`server.services.admission.storyboard_batch`),
  filling only missing usable images. Skip missing prompts or unavailable references; submit the rest normally.
- Videos fill only storyboards without usable videos. Skip missing prompts/images and active generation; admit
  the remaining batch atomically. If any target fails admission, reject the whole batch with no tasks and per-item reasons.

Both separate preview from submission: preview only plans, creating no tasks, for confirmation lists, skipped items,
estimated costs of priced items and the count of unpriced items. Submission replans, creates a durable batch, and returns
immediately. Stale artifacts are excluded from batch generation."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.generation.generation_queue import get_generation_queue
from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError
from lib.project.project_manager import get_project_manager
from lib.script.script_skeleton import SkeletonRouteMismatchError
from server.auth import CurrentUser
from server.i18n import DisplayNamesCatalog, Translator
from server.routers._batch_admission import enqueue_failure_payload, localized_admission_payload
from server.services.admission.cost_estimation import estimate_image_batch_cost
from server.services.admission.storyboard_batch import (
    BatchSkip,
    EpisodeStoryboardScript,
    StoryboardBatchEpisodeNotFound,
    StoryboardBatchRouteMismatch,
    StoryboardImageBatchPlan,
    StoryboardVideoBatchPlan,
    estimate_storyboard_video_batch_cost,
    load_storyboard_image_batch_plan,
    load_storyboard_video_batch_plan,
    submit_storyboard_image_batch,
    submit_storyboard_video_batch,
)
from server.services.tasks.formal_image_commit import get_aspect_ratio

router = APIRouter(prefix="/projects/{project_name}/episodes/{episode}")


def _skipped(skips: tuple[BatchSkip, ...]) -> list[dict[str, str]]:
    return [{"unit_id": skip.unit_id, "reason": skip.reason.value} for skip in skips]


@contextmanager
def _planning_errors(project_name: str, episode: int) -> Generator[None]:
    """Map planning failures in project/script reads and generation-mode guards to HTTP errors."""

    try:
        yield
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except StoryboardBatchEpisodeNotFound as exc:
        raise NotFoundError("episode_not_found", episode=exc.episode_id) from exc
    except StoryboardBatchRouteMismatch as exc:
        raise ConflictError("video_route_is_reference_video") from exc
    except (SkeletonRouteMismatchError, ValueError) as exc:
        raise BadRequestError("storyboard_batch_script_invalid", episode=episode) from exc


async def _image_plan(
    project_name: str, episode: int, user_id: str
) -> tuple[EpisodeStoryboardScript, StoryboardImageBatchPlan]:
    with _planning_errors(project_name, episode):
        return await load_storyboard_image_batch_plan(
            get_project_manager(),
            get_generation_queue(),
            project_name=project_name,
            episode_id=episode,
            user_id=user_id,
        )


async def _video_plan(project_name: str, episode: int, user_id: str) -> StoryboardVideoBatchPlan:
    with _planning_errors(project_name, episode):
        return await load_storyboard_video_batch_plan(
            get_project_manager(),
            get_generation_queue(),
            project_name=project_name,
            episode_id=episode,
            user_id=user_id,
            config_resolver=ConfigResolver(async_session_factory),
        )


@router.post("/storyboards/batch/preview")
async def preview_storyboard_batch(project_name: str, episode: int, user: CurrentUser) -> dict[str, Any]:
    """Plan storyboard images without tasks: generation targets, skipped items, estimated costs of priced items and the count of unpriced items."""

    loaded, plan = await _image_plan(project_name, episode, user.id)
    new_ids = plan.new_task_ids
    estimated = await estimate_image_batch_cost(
        loaded.project,
        [plan.lanes[unit_id] for unit_id in new_ids],
        aspect_ratio=get_aspect_ratio(loaded.project, "storyboards"),
        resolver=ConfigResolver(async_session_factory),
        session_factory=async_session_factory,
    )
    return {
        "targets": [{"unit_id": unit_id} for unit_id in new_ids],
        "skipped": _skipped(plan.skips),
        **estimated.to_payload(),
    }


@router.post("/storyboards/batch")
async def submit_storyboard_batch(project_name: str, episode: int, user: CurrentUser, _t: Translator) -> dict[str, Any]:
    """Submit storyboard images and return immediately; adjacent shots queue within the batch according to the reference chain."""

    _loaded, plan = await _image_plan(project_name, episode, user.id)
    batch, enqueued, failures = await submit_storyboard_image_batch(
        plan, project_name=project_name, queue=get_generation_queue(), source="webui", user_id=user.id
    )
    return {
        "batch_id": batch.batch_id,
        "task_ids_by_unit": {task.resource_id: task.task_id for task in enqueued},
        "skipped": _skipped(plan.skips),
        "enqueue_failures": [enqueue_failure_payload(failure, _t) for failure in failures],
    }


@router.post("/videos/batch/preview")
async def preview_storyboard_video_batch(
    project_name: str, episode: int, user: CurrentUser, _t: Translator, names: DisplayNamesCatalog
) -> dict[str, Any]:
    """Plan storyboard videos without tasks: generation targets, skipped items, whole-batch admission, estimated costs of priced items and the count of unpriced items."""

    plan = await _video_plan(project_name, episode, user.id)
    estimated = await estimate_storyboard_video_batch_cost(plan, async_session_factory)
    return {
        "targets": [{"unit_id": unit_id} for unit_id in plan.target_ids],
        "skipped": _skipped(plan.skips),
        "admission": localized_admission_payload(plan.admission, _t, names),
        **estimated.to_payload(),
    }


@router.post("/videos/batch")
async def submit_storyboard_video_batch_route(
    project_name: str, episode: int, user: CurrentUser, _t: Translator, names: DisplayNamesCatalog
) -> dict[str, Any]:
    """Replan and submit storyboard videos. Failed whole-batch admission returns the decision with HTTP 200 and creates no tasks."""

    plan = await _video_plan(project_name, episode, user.id)
    payload: dict[str, Any] = {
        "admission": localized_admission_payload(plan.admission, _t, names),
        "skipped": _skipped(plan.skips),
        "batch_id": None,
        "task_ids_by_unit": {},
        "enqueue_failures": [],
    }
    if not plan.admission.admitted or not plan.specs:
        return payload
    batch, enqueued, failures = await submit_storyboard_video_batch(
        plan, project_name=project_name, queue=get_generation_queue(), source="webui", user_id=user.id
    )
    payload["batch_id"] = batch.batch_id
    payload["task_ids_by_unit"] = {task.resource_id: task.task_id for task in enqueued}
    payload["enqueue_failures"] = [enqueue_failure_payload(failure, _t) for failure in failures]
    return payload


__all__ = ["router"]
