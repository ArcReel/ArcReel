"""一集分镜图与分镜视频的批量生成入口（时间线与宫格工具栏）。

- 分镜图：与 Agent 的 ``generate_storyboards`` 同一份规划（:mod:`server.services.admission.storyboard_batch`），
  只补本集没有可用分镜图的分镜；缺提示词、引用不可用的分镜列为跳过项，其余照常提交。
- 分镜视频：只补本集没有可用视频的分镜；缺提示词、缺分镜图、已在生成中的分镜列为跳过项，其余分镜整批准入，
  任一目标不满足时整批拒绝，一个任务也不建，并逐项说明原因。

两者都分预览与提交：预览只规划不建任务，供确认框列名单、跳过项与能算出时的预估费用；提交重新规划后建一个
持久批次，立即返回。过期的产物不进批量。
"""

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

router = APIRouter(prefix="/projects/{project_name}/episodes/{episode}")


def _skipped(skips: tuple[BatchSkip, ...]) -> list[dict[str, str]]:
    return [{"unit_id": skip.unit_id, "reason": skip.reason.value} for skip in skips]


@contextmanager
def _planning_errors(project_name: str, episode: int) -> Generator[None]:
    """把规划阶段读项目、读剧本与把守生成模式的失败映射成 HTTP 错误。"""

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
    """规划一批分镜图但不建任务：要生成的分镜、跳过项与能算出时的预估费用。"""

    loaded, plan = await _image_plan(project_name, episode, user.id)
    new_ids = plan.new_task_ids
    estimated = await estimate_image_batch_cost(
        loaded.project,
        [plan.lanes[unit_id] for unit_id in new_ids],
        resolver=ConfigResolver(async_session_factory),
        session_factory=async_session_factory,
    )
    return {
        "targets": [{"unit_id": unit_id} for unit_id in new_ids],
        "skipped": _skipped(plan.skips),
        "estimated_cost": estimated or None,
    }


@router.post("/storyboards/batch")
async def submit_storyboard_batch(project_name: str, episode: int, user: CurrentUser, _t: Translator) -> dict[str, Any]:
    """提交一批分镜图，立即返回；相邻分镜在批内按参考链排队。"""

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
    """规划一批分镜视频但不建任务：要生成的分镜、跳过项、整批准入结论与能算出时的预估费用。"""

    plan = await _video_plan(project_name, episode, user.id)
    estimated = await estimate_storyboard_video_batch_cost(plan, async_session_factory)
    return {
        "targets": [{"unit_id": unit_id} for unit_id in plan.target_ids],
        "skipped": _skipped(plan.skips),
        "admission": localized_admission_payload(plan.admission, _t, names),
        "estimated_cost": estimated or None,
    }


@router.post("/videos/batch")
async def submit_storyboard_video_batch_route(
    project_name: str, episode: int, user: CurrentUser, _t: Translator, names: DisplayNamesCatalog
) -> dict[str, Any]:
    """重新规划后提交一批分镜视频。整批准入未通过时以 200 返回结论、一个任务也不建。"""

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
