"""Mandatory Video Unit Storyboard Sheet generation and review gate."""

from __future__ import annotations

import asyncio
import math
from copy import deepcopy
from datetime import UTC, datetime
from math import gcd
from typing import Any

from lib.db.base import DEFAULT_USER_ID
from lib.generation_queue_client import TaskSpec, batch_enqueue_only
from lib.image_reference_snapshot import freeze_image_references
from lib.path_safety import PathTraversalError, safe_join
from lib.reference_video.request_projection import resolve_reference_assets
from lib.resource_paths import resource_relative_path
from lib.video_visual_provenance import resolve_video_aspect_ratio
from server.services.generation_context import ImageLaneRequest, resolve_generation_context
from server.services.generation_tasks import get_project_manager
from server.services.reference_image_binding import (
    bind_resolved_assets,
    prompt_roster,
    provider_inputs,
    visual_references,
)
from server.services.video_caps import project_video_caps


class StoryboardSheetGateError(ValueError):
    """A stable code-backed error raised by the mandatory review gate."""

    def __init__(self, code: str, **params: object) -> None:
        self.code = code
        self.params = params
        super().__init__(code)


def reference_storyboard_sheet_task_specs(
    script: dict[str, Any],
    script_file: str,
    *,
    unit_ids: set[str] | None = None,
    missing_only: bool = False,
    image_override: dict[str, str] | None = None,
) -> list[TaskSpec]:
    specs: list[TaskSpec] = []
    for unit in script.get("video_units") or []:
        if not isinstance(unit, dict):
            continue
        unit_id = str(unit.get("unit_id") or "").strip()
        text = str(unit.get("text") or "").strip()
        if not unit_id or not text or (unit_ids is not None and unit_id not in unit_ids):
            continue
        sheet = unit.get("storyboard_sheet")
        if missing_only and isinstance(sheet, dict) and sheet.get("image_path"):
            continue
        specs.append(
            TaskSpec.from_request(
                task_type="reference_storyboard_sheet",
                media_type="image",
                resource_id=unit_id,
                prompt=text,
                script_file=script_file,
                extra_payload=image_override or {},
            )
        )
    return specs


def _panel_count(unit: dict[str, Any]) -> int:
    duration = int(unit.get("duration_seconds") or 8)
    return max(4, min(6, math.ceil(duration / 2)))


def _sheet_aspect_ratio(panel_ratio: str, panel_count: int) -> str:
    """Auto-layout the outer canvas while preserving each cell's project ratio."""

    try:
        width_text, height_text = panel_ratio.split(":", 1)
        panel_width, panel_height = int(width_text), int(height_text)
        if panel_width <= 0 or panel_height <= 0:
            raise ValueError
    except (TypeError, ValueError):
        panel_width, panel_height = 9, 16
    columns = 3 if panel_count > 4 else 2
    rows = math.ceil(panel_count / columns)
    width = columns * panel_width
    height = rows * panel_height
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def build_storyboard_sheet_prompt(
    project: dict[str, Any],
    unit: dict[str, Any],
    *,
    panel_ratio: str,
    panel_count: int,
    reference_roster: str,
) -> str:
    keyframes = [
        str(item.get("description") or "").strip()
        for item in unit.get("keyframes") or []
        if isinstance(item, dict) and str(item.get("description") or "").strip()
    ]
    keyframe_plan = "\n".join(f"- {item}" for item in keyframes) or "- 按正文识别核心动作与场景 beat"
    return f"""生成一张 Video Unit Storyboard Sheet，内容覆盖完整 Video Unit，而不是单一成片画面。

Video Unit：{unit.get("unit_id")}
项目风格：{str(project.get("style") or "").strip()}
风格定义：{str(project.get("style_description") or "").strip()}
正文：
{str(unit.get("text") or "").strip()}

核心场景规划：
{keyframe_plan}

真实参考图绑定（必须严格按 Picture 编号使用，不得把 @ 文本画进图里）：
{reference_roster}

版式要求：
- 输出一张 Video Unit Storyboard Sheet，外层画布按内容自动排版。
- 恰好包含 {panel_count} 个清晰分隔的 panel，按叙事发生顺序从左到右、从上到下排列。
- 每个单独 panel 的画面比例必须是 {panel_ratio}；不得把外层 Sheet 比例误当成 panel 比例。
- panel 共同覆盖该 Video Unit 的完整动作进程、场景切换和镜头关系。
- 每个动作 beat 的入口 panel 描绘动作刚开始的稳定可见状态，不把同一 beat 的完成结果当作入口帧。
- 例如“妹妹追弟弟，弟弟绊倒摔进桂花堆”：入口 panel 是妹妹开始追、弟弟开始逃；摔入花堆只能作为后续发展或结果 panel。
- 使用简洁专业的分镜草图语言，可带镜号和极短技术标注；不要生成大段正文、品牌、水印或无关装饰。
"""


def _load_unit(project_name: str, script_file: str, unit_id: str) -> tuple[dict, dict, dict]:
    pm = get_project_manager()
    project = pm.load_project(project_name)
    script = pm.load_script(project_name, script_file)
    unit = next(
        (
            candidate
            for candidate in script.get("video_units") or []
            if isinstance(candidate, dict) and candidate.get("unit_id") == unit_id
        ),
        None,
    )
    if unit is None:
        raise StoryboardSheetGateError("ref_unit_not_found", unit_id=unit_id)
    return project, script, unit


def _unit_storyboard_basis(unit: dict[str, Any]) -> tuple[object, object, object]:
    return unit.get("text"), unit.get("duration_seconds"), unit.get("keyframes")


def _assert_unit_unchanged(
    project_name: str, script_file: str, unit_id: str, expected_basis: tuple[object, object, object]
) -> None:
    _project, _script, unit = _load_unit(project_name, script_file, unit_id)
    if _unit_storyboard_basis(unit) != expected_basis:
        raise StoryboardSheetGateError("reference_storyboard_sheet_input_changed", unit_id=unit_id)


def _commit_sheet_pointer(
    project_name: str,
    script_file: str,
    unit_id: str,
    expected_basis: tuple[object, object, object],
    image_path: str,
) -> None:
    pm = get_project_manager()
    with pm.locked_script(project_name, script_file, validate=False) as script:
        unit = next(
            (
                candidate
                for candidate in script.get("video_units") or []
                if isinstance(candidate, dict) and candidate.get("unit_id") == unit_id
            ),
            None,
        )
        if unit is None:
            raise StoryboardSheetGateError("ref_unit_not_found", unit_id=unit_id)
        if _unit_storyboard_basis(unit) != expected_basis:
            raise StoryboardSheetGateError("reference_storyboard_sheet_input_changed", unit_id=unit_id)
        unit["storyboard_sheet"] = {
            "image_path": image_path,
            "status": "pending_review",
            "confirmed_at": None,
        }


async def execute_reference_storyboard_sheet_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    del task_id
    script_file = str(payload.get("script_file") or "").strip()
    if not script_file:
        raise ValueError("script_file is required for reference_storyboard_sheet task")
    project, _script, unit = await asyncio.to_thread(_load_unit, project_name, script_file, resource_id)
    expected_basis = deepcopy(_unit_storyboard_basis(unit))
    project_path = get_project_manager().get_project_path(project_name)
    resolved = await asyncio.to_thread(resolve_reference_assets, project, project_path, unit)
    assets = tuple(asset for asset in resolved if asset.reference.type not in {"keyframe", "storyboard_sheet"})
    bindings = bind_resolved_assets(assets)
    frozen = freeze_image_references(provider_inputs(bindings), visual_references(bindings, role="storyboard_subject"))
    try:
        ctx = await resolve_generation_context(
            project_name,
            payload,
            project=project,
            user_id=user_id,
            image=ImageLaneRequest(capability="i2i" if bindings else "t2i"),
        )
        panel_ratio = resolve_video_aspect_ratio(project)
        count = _panel_count(unit)

        async def _before_submit() -> None:
            await asyncio.to_thread(_assert_unit_unchanged, project_name, script_file, resource_id, expected_basis)

        image_path = resource_relative_path("storyboard_sheets", resource_id)
        _output, version = await ctx.generator.generate_image_async(
            prompt=build_storyboard_sheet_prompt(
                project,
                unit,
                panel_ratio=panel_ratio,
                panel_count=count,
                reference_roster=prompt_roster(bindings),
            ),
            resource_type="storyboard_sheets",
            resource_id=resource_id,
            reference_images=frozen.reference_images,
            aspect_ratio=_sheet_aspect_ratio(panel_ratio, count),
            image_size=None,
            before_submit=_before_submit,
            source="reference_storyboard_sheet",
            script_file=script_file,
            panel_aspect_ratio=panel_ratio,
            panel_count=count,
        )
    finally:
        await asyncio.to_thread(frozen.cleanup)
    await asyncio.to_thread(
        _commit_sheet_pointer,
        project_name,
        script_file,
        resource_id,
        expected_basis,
        image_path,
    )
    return {
        "version": version,
        "file_path": image_path,
        "resource_type": "storyboard_sheets",
        "resource_id": resource_id,
    }


def require_confirmed_storyboard_sheet(unit: dict[str, Any]) -> dict[str, Any]:
    sheet = unit.get("storyboard_sheet")
    if not isinstance(sheet, dict) or not str(sheet.get("image_path") or "").strip():
        raise StoryboardSheetGateError("reference_storyboard_sheet_required", unit_id=str(unit.get("unit_id") or ""))
    if sheet.get("status") != "confirmed":
        raise StoryboardSheetGateError(
            "reference_storyboard_sheet_confirmation_required", unit_id=str(unit.get("unit_id") or "")
        )
    return sheet


def require_keyframe_plan(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """Require at least one addressable keyframe before leaving the Sheet gate."""

    keyframes = [
        item
        for item in unit.get("keyframes") or []
        if isinstance(item, dict)
        and isinstance(item.get("keyframe_id"), str)
        and str(item.get("keyframe_id")).strip()
        and str(item.get("description") or "").strip()
    ]
    if not keyframes:
        raise StoryboardSheetGateError("reference_keyframe_plan_required", unit_id=str(unit.get("unit_id") or ""))
    return keyframes


def require_generated_keyframes(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """Require every planned keyframe to have an activated image pointer."""

    keyframes = require_keyframe_plan(unit)
    missing_ids = [
        str(item.get("keyframe_id") or "")
        for item in keyframes
        if not str(item.get("image_path") or "").strip()
    ]
    if missing_ids:
        raise StoryboardSheetGateError(
            "reference_keyframe_images_required",
            unit_id=str(unit.get("unit_id") or ""),
            keyframe_ids=", ".join(missing_ids),
        )
    return keyframes


async def confirm_storyboard_sheet_and_enqueue_keyframes(
    project_name: str,
    script_file: str,
    unit_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> tuple[dict[str, Any], list[str]]:
    """Confirm the current Sheet and queue all keyframes through one shared operation."""

    project, _script, unit = await asyncio.to_thread(_load_unit, project_name, script_file, unit_id)
    planned_keyframes = require_keyframe_plan(unit)
    sheet = unit.get("storyboard_sheet")
    if not isinstance(sheet, dict) or not str(sheet.get("image_path") or "").strip():
        raise StoryboardSheetGateError("reference_storyboard_sheet_required", unit_id=unit_id)
    project_path = get_project_manager().get_project_path(project_name)
    try:
        sheet_path = safe_join(project_path, str(sheet["image_path"]), require_file=True)
    except (FileNotFoundError, PathTraversalError):
        raise StoryboardSheetGateError("reference_storyboard_sheet_required", unit_id=unit_id) from None
    if not sheet_path.is_file():
        raise StoryboardSheetGateError("reference_storyboard_sheet_required", unit_id=unit_id)

    candidate = deepcopy(unit)
    candidate["storyboard_sheet"]["status"] = "confirmed"
    available = [asset for asset in resolve_reference_assets(project, project_path, candidate) if asset.path.is_file()]
    non_keyframe_count = sum(asset.reference.type != "keyframe" for asset in available)
    keyframe_count = len(planned_keyframes)
    projected_reference_count = non_keyframe_count + keyframe_count
    caps = await project_video_caps(project, degraded_to="Video Unit Storyboard Sheet 确认只校验可解析的参考图上限")
    max_references = caps.get("max_reference_images")
    if isinstance(max_references, int) and projected_reference_count > max_references:
        raise StoryboardSheetGateError(
            "reference_storyboard_sheet_reference_limit",
            unit_id=unit_id,
            count=projected_reference_count,
            max_count=max_references,
        )

    confirmed_at = datetime.now(UTC).isoformat()
    pm = get_project_manager()
    with pm.locked_script(project_name, script_file, validate=False) as current:
        current_unit = next(
            (
                item
                for item in current.get("video_units") or []
                if isinstance(item, dict) and item.get("unit_id") == unit_id
            ),
            None,
        )
        if current_unit is None or current_unit.get("storyboard_sheet") != sheet:
            raise StoryboardSheetGateError("reference_storyboard_sheet_changed", unit_id=unit_id)
        current_unit["storyboard_sheet"]["status"] = "confirmed"
        current_unit["storyboard_sheet"]["confirmed_at"] = confirmed_at
        confirmed_unit = deepcopy(current_unit)

    from server.services.reference_keyframe_tasks import reference_keyframe_task_specs

    specs = reference_keyframe_task_specs({"video_units": [confirmed_unit]}, script_file, missing_only=False)
    enqueued, failures = await batch_enqueue_only(project_name=project_name, specs=specs, user_id=user_id)
    if failures:
        raise StoryboardSheetGateError(
            "reference_storyboard_sheet_keyframes_enqueue_failed",
            unit_id=unit_id,
            failed_count=len(failures),
        )
    return confirmed_unit["storyboard_sheet"], [item.task_id for item in enqueued]


__all__ = [
    "StoryboardSheetGateError",
    "build_storyboard_sheet_prompt",
    "confirm_storyboard_sheet_and_enqueue_keyframes",
    "execute_reference_storyboard_sheet_task",
    "reference_storyboard_sheet_task_specs",
    "require_confirmed_storyboard_sheet",
    "require_generated_keyframes",
    "require_keyframe_plan",
]
