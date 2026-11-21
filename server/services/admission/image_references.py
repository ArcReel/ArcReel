"""图片生成准入与规划复用执行器的模型上限和参考图装配。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from lib.artifacts.generation_input import (
    asset_sheet_input,
    grid_references,
    project_input_observation,
    storyboard_image_input,
)
from lib.artifacts.visual_artifact_provenance import VisualReference
from lib.backends.image_errors import ImageCapabilityError
from lib.config.resolver import ImageBucketCapabilityError
from lib.generation.generation_result import GenerationAction, GenerationProblem
from lib.i18n import _
from lib.project.resource_paths import resource_relative_path
from lib.prompts.reference_image_numbering import PREVIOUS_STORYBOARD_ROLE, require_reference_image_limit
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
        # 未配置生成通道的项目仍允许只读预览，执行器会报告配置缺口。
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
        # 衍生固定使用一张本体资产图，批内同时生成本体时也要为它预留位置。
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


def check_storyboard_image_limit(
    project: Mapping[str, Any],
    script: dict[str, Any],
    project_path: Path,
    *,
    episode: int,
    resource_id: str,
    limit: tuple[int | None, str],
    pending_previous_id: str | None = None,
) -> None:
    """按本次计划携带的参考图检查单分镜上限，计入已就绪或生成中的上一分镜图。"""
    maximum, model = limit
    if maximum is None:
        return
    assembled = storyboard_image_input(
        project,
        script,
        episode=episode,
        resource_id=resource_id,
        observation=project_input_observation(project_path),
    )
    visuals = [ref.visual for ref in assembled.references]
    if pending_previous_id and not any(ref.role == PREVIOUS_STORYBOARD_ROLE for ref in visuals):
        expected_rel = resource_relative_path("storyboards", pending_previous_id)
        if expected_rel not in {ref.artifact_path for ref in assembled.references}:
            visuals.append(
                VisualReference(
                    project_path / expected_rel,
                    role=PREVIOUS_STORYBOARD_ROLE,
                    logical_type="storyboard",
                    logical_id=pending_previous_id,
                )
            )
    require_reference_image_limit(visuals, maximum, model=model)
