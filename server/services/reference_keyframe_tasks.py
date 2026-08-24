"""Generation and script-pointer updates for reference-video keyframe images."""

from __future__ import annotations

import asyncio
from typing import Any

from lib.db.base import DEFAULT_USER_ID
from lib.generation_queue_client import TaskSpec
from lib.image_reference_snapshot import freeze_image_references
from lib.path_safety import safe_join
from lib.reference_video.keyframes import find_keyframe
from lib.reference_video.request_projection import resolve_reference_assets
from lib.resource_paths import resource_relative_path
from server.services.generation_context import ImageLaneRequest, resolve_generation_context
from server.services.generation_tasks import get_aspect_ratio, get_project_manager
from server.services.reference_image_binding import (
    prepend_storyboard_sheet,
    prompt_roster,
    provider_inputs,
    visual_references,
)


def reference_keyframe_task_specs(
    script: dict[str, Any],
    script_file: str,
    *,
    keyframe_ids: set[str] | None = None,
    missing_only: bool = False,
    image_override: dict[str, str] | None = None,
) -> list[TaskSpec]:
    """Build generation specs in script order for Web and Agent batch entry points."""

    specs: list[TaskSpec] = []
    for unit in script.get("video_units") or []:
        if not isinstance(unit, dict):
            continue
        for keyframe in unit.get("keyframes") or []:
            if not isinstance(keyframe, dict):
                continue
            value = keyframe.get("keyframe_id")
            description = str(keyframe.get("description") or "").strip()
            if not isinstance(value, str) or not value or not description:
                continue
            if keyframe_ids is not None and value not in keyframe_ids:
                continue
            if missing_only and keyframe.get("image_path"):
                continue
            from server.services.reference_storyboard_sheet_tasks import require_confirmed_storyboard_sheet

            require_confirmed_storyboard_sheet(unit)
            specs.append(
                TaskSpec.from_request(
                    task_type="reference_keyframe",
                    media_type="image",
                    resource_id=value,
                    prompt=description,
                    script_file=script_file,
                    extra_payload={
                        "unit_id": str(unit.get("unit_id") or ""),
                        **(image_override or {}),
                    },
                )
            )
    return specs


def build_keyframe_prompt(project: dict[str, Any], description: str, reference_roster: str) -> str:
    style = str(project.get("style") or "").strip()
    style_description = str(project.get("style_description") or "").strip()
    return (
        "生成视频关键分镜的第一帧静态画面。输出单张画面，不生成 Storyboard、文字、水印或拼图。\n"
        "这张图必须是当前核心动作或场景 beat 的入口状态：呈现动作刚开始、意图和方向已经可见的第一个稳定瞬间；"
        "不得选择同一 beat 的完成结果、撞击后、摔倒后或事后状态。只有结果本身开启新的 beat 时，才可作为下一关键帧。\n"
        f"项目风格：{style}\n"
        f"风格定义：{style_description}\n"
        "真实参考图绑定（必须按 Picture 编号使用；@ 名称不是画面文字）：\n"
        f"{reference_roster}\n"
        f"首帧描述：{description.strip()}"
    )


def _load_keyframe(project_name: str, script_file: str, keyframe_id: str) -> tuple[dict, dict, dict, dict]:
    pm = get_project_manager()
    project = pm.load_project(project_name)
    script = pm.load_script(project_name, script_file)
    found = find_keyframe(script, keyframe_id)
    if found is None:
        raise ValueError(f"reference keyframe not found: {keyframe_id}")
    unit, keyframe = found
    return project, script, unit, keyframe


def _assert_keyframe_unchanged(
    project_name: str,
    script_file: str,
    keyframe_id: str,
    expected_description: str,
) -> None:
    _project, _script, _unit, keyframe = _load_keyframe(project_name, script_file, keyframe_id)
    if str(keyframe.get("description") or "").strip() != expected_description:
        raise ValueError(f"reference keyframe changed while generation was pending: {keyframe_id}")


def _commit_keyframe_pointer(
    project_name: str,
    script_file: str,
    keyframe_id: str,
    expected_description: str,
    image_path: str,
) -> None:
    pm = get_project_manager()
    with pm.locked_script(project_name, script_file, validate=False) as script:
        found = find_keyframe(script, keyframe_id)
        if found is None:
            raise ValueError(f"reference keyframe no longer exists: {keyframe_id}")
        _unit, keyframe = found
        if str(keyframe.get("description") or "").strip() != expected_description:
            raise ValueError(f"reference keyframe changed before image activation: {keyframe_id}")
        keyframe["image_path"] = image_path


async def execute_reference_keyframe_task(
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
        raise ValueError("script_file is required for reference_keyframe task")

    project, _script, _unit, keyframe = await asyncio.to_thread(_load_keyframe, project_name, script_file, resource_id)
    description = str(keyframe.get("description") or "").strip()
    if not description:
        raise ValueError("reference keyframe description is required")

    project_path = get_project_manager().get_project_path(project_name)
    reference_assets = await asyncio.to_thread(
        resolve_reference_assets,
        project,
        project_path,
        {"text": description, "keyframes": []},
    )
    from server.services.reference_storyboard_sheet_tasks import require_confirmed_storyboard_sheet

    sheet = require_confirmed_storyboard_sheet(_unit)
    sheet_path = safe_join(project_path, str(sheet["image_path"]))
    bindings = prepend_storyboard_sheet(str(_unit.get("unit_id") or ""), sheet_path, reference_assets)
    frozen = freeze_image_references(
        provider_inputs(bindings),
        visual_references(bindings, role="keyframe_subject"),
    )
    try:
        ctx = await resolve_generation_context(
            project_name,
            payload,
            project=project,
            user_id=user_id,
            image=ImageLaneRequest(capability="i2i" if frozen.reference_images else "t2i"),
        )

        async def _before_submit() -> None:
            await asyncio.to_thread(
                _assert_keyframe_unchanged,
                project_name,
                script_file,
                resource_id,
                description,
            )

        image_path = resource_relative_path("keyframes", resource_id)
        _output, version = await ctx.generator.generate_image_async(
            prompt=build_keyframe_prompt(project, description, prompt_roster(bindings)),
            resource_type="keyframes",
            resource_id=resource_id,
            reference_images=frozen.reference_images,
            aspect_ratio=get_aspect_ratio(project, "storyboards"),
            image_size=ctx.image.resolution,
            before_submit=_before_submit,
            source="reference_keyframe",
            script_file=script_file,
        )
    finally:
        await asyncio.to_thread(frozen.cleanup)

    await asyncio.to_thread(
        _commit_keyframe_pointer,
        project_name,
        script_file,
        resource_id,
        description,
        image_path,
    )
    versions = await asyncio.to_thread(ctx.generator.versions.get_versions, "keyframes", resource_id)
    records = versions.get("versions") if isinstance(versions, dict) else None
    created_at = records[-1].get("created_at") if isinstance(records, list) and records else None
    return {
        "version": version,
        "file_path": image_path,
        "created_at": created_at,
        "resource_type": "keyframes",
        "resource_id": resource_id,
    }
