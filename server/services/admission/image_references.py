"""图片生成准入与规划复用执行器的模型上限和参考图装配。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence, Set
from pathlib import Path
from typing import Any, Literal

from lib.artifacts.generation_input import (
    InputObservation,
    asset_sheet_input,
    grid_references,
    project_input_observation,
    storyboard_image_input,
)
from lib.artifacts.visual_artifact_provenance import VisualReference
from lib.backends.image_errors import ImageCapabilityError
from lib.config.resolver import ImageBucketCapabilityError
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_result import GenerationAction, GenerationProblem
from lib.i18n import _
from lib.project.resource_paths import resource_relative_path
from lib.prompts.reference_image_numbering import PREVIOUS_STORYBOARD_ROLE, require_reference_image_limit
from lib.script.storyboard_sequence import find_storyboard_item, get_storyboard_items
from server.services.tasks.generation_context import ImageLaneRequest, ImageLaneResult, resolve_generation_context


async def project_image_lane(
    project_name: str, project: dict, project_path: Path, bucket: Literal["t2i", "i2i"]
) -> ImageLaneResult:
    ctx = await resolve_generation_context(
        project_name, None, project=project, project_path=project_path, image=ImageLaneRequest(generation_type=bucket)
    )
    return ctx.image


async def reference_image_limit(
    project_name: str, project: dict[str, Any], project_path: Path
) -> tuple[int | None, str]:
    try:
        lane = await project_image_lane(project_name, project, project_path, "i2i")
    except ImageBucketCapabilityError as exc:
        return 0, exc.model_id
    except Exception as exc:
        logging.getLogger(__name__).info("无法解析图生图通道：%s", exc)
        return None, ""
    return lane.max_reference_images, lane.backend_model


def limit_problem(exc: ImageCapabilityError) -> GenerationProblem:
    return GenerationProblem(
        code=exc.code, detail=_(exc.code, **exc.params), action=GenerationAction.FIX_INPUT, params=exc.params
    )


def check_grid_image_limit(
    project: dict, script: dict, project_path: Path, members: Sequence[str], limit: tuple[int | None, str]
) -> int:
    maximum, model = limit
    assembled = grid_references(
        project, script, member_ids=members, observation=project_input_observation(project_path)
    )
    require_reference_image_limit([ref.visual for ref in assembled.references], maximum, model=model)
    return len(assembled.references)


def check_asset_image_limit(
    project: Mapping[str, Any],
    project_path: Path,
    asset_type: str,
    name: str,
    *,
    owner: str | None,
    limit: tuple[int | None, str],
) -> None:
    maximum, model = limit
    if maximum is None:
        return
    if owner is not None:
        visuals = [VisualReference(project_path, "asset_sheet", "character", owner)]
    else:
        assembled = asset_sheet_input(
            project, asset_type=asset_type, name=name, observation=project_input_observation(project_path)
        )
        visuals = [ref.visual for ref in assembled.references]
    require_reference_image_limit(visuals, maximum, model=model)


def planned_previous_storyboard_id(items: Sequence[Any], id_field: str, index: int) -> str | None:
    """按分镜连贯规则返回本分镜应衔接的上一分镜 ID；首镜或章节切分点返回 ``None``。"""
    if index <= 0:
        return None
    item = items[index]
    if not isinstance(item, Mapping) or item.get("segment_break"):
        return None
    previous_item = items[index - 1]
    if not isinstance(previous_item, Mapping):
        return None
    return str(previous_item.get(id_field) or "").strip() or None


def planned_previous_storyboard_ids(script: dict, requested_ids: Sequence[str] | None) -> list[str]:
    items, id_field, *_ = get_storyboard_items(script)
    requested = set(requested_ids) if requested_ids is not None else None
    return [
        previous_id
        for index, item in enumerate(items)
        if (requested is None or str(item.get(id_field)) in requested)
        and (previous_id := planned_previous_storyboard_id(items, id_field, index)) is not None
    ]


async def active_storyboard_ids(
    queue: GenerationQueue, *, project_name: str, script_file: str, resource_ids: list[str], user_id: str
) -> frozenset[str]:
    if not resource_ids:
        return frozenset()
    tasks = await queue.get_active_tasks_for_resources(
        project_name=project_name,
        task_type="storyboard",
        resource_ids=resource_ids,
        script_file=script_file,
        user_id=user_id,
    )
    return frozenset(str(task["resource_id"]) for task in tasks if task.get("resource_id"))


def check_storyboard_image_limit(
    project: Mapping[str, Any],
    script: dict[str, Any],
    project_path: Path,
    *,
    episode: int,
    resource_id: str,
    limit: tuple[int | None, str],
    planned_ids: Set[str] = frozenset(),
    observation: InputObservation | None = None,
) -> None:
    """检查预计携带的参考图；``planned_ids`` 包含在途和本批已放行的分镜。"""
    maximum, model = limit
    if maximum is None:
        return
    assembled = storyboard_image_input(
        project,
        script,
        episode=episode,
        resource_id=resource_id,
        observation=observation if observation is not None else project_input_observation(project_path),
    )
    visuals = [ref.visual for ref in assembled.references]
    if planned_ids and not any(ref.role == PREVIOUS_STORYBOARD_ROLE for ref in visuals):
        items, id_field, *_ = get_storyboard_items(script)
        resolved = find_storyboard_item(items, id_field, resource_id)
        previous_id = planned_previous_storyboard_id(items, id_field, resolved[1]) if resolved else None
        if previous_id is not None and previous_id in planned_ids:
            expected_rel = resource_relative_path("storyboards", previous_id)
            if expected_rel not in {ref.artifact_path for ref in assembled.references}:
                visuals.append(
                    VisualReference(
                        project_path / expected_rel,
                        role=PREVIOUS_STORYBOARD_ROLE,
                        logical_type="storyboard",
                        logical_id=previous_id,
                    )
                )
    require_reference_image_limit(visuals, maximum, model=model)
