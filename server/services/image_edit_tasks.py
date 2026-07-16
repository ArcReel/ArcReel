"""图片指令式编辑（image edit）任务执行层。

编辑语义（见 ``docs/adr/0049`` 与 CONTEXT.md「图片编辑」术语）：以资源的当前图为唯一
参考图、用户编辑指令为唯一 prompt 调用 i2i，产出新图覆盖 current 并自动进版本历史；
原 image_prompt 不回写——编辑是对**图**的分叉而非对 prompt 的分叉。

支持的资源：character / scene / prop / product 四类设计图 + storyboard 分镜图。
「当前图路径解析」与「按资源类型写回」复用生成链路的既有口径（资产 sheet 字段 /
剧本 generated_assets），由 :func:`resolve_current_image_rel` 统一提供给路由
（入队前校验）与 executor（执行时读取），两侧口径不分叉。
"""

from __future__ import annotations

import asyncio
from typing import Any

from lib.asset_types import ASSET_SPECS
from lib.db.base import DEFAULT_USER_ID
from lib.path_safety import safe_exists
from lib.resource_paths import resource_relative_path
from lib.storyboard_sequence import find_storyboard_item, get_storyboard_items
from server.services.generation_tasks import (
    _resolve_effective_image_backend,
    _resolve_resolution,
    get_aspect_ratio,
    get_media_generator,
    get_project_manager,
)

# 版本记录里标记「指令式编辑」的 source 值；前端据此展示编辑标记（与 manual_upload 同机制）
IMAGE_EDIT_VERSION_SOURCE = "image_edit"

# 可编辑的资源类型白名单（API 契约用的单数形态；storyboard 之外与 ASSET_SPECS 同源）
EDITABLE_RESOURCE_TYPES: tuple[str, ...] = (*ASSET_SPECS.keys(), "storyboard")


def edit_version_resource_type(resource_type: str) -> str:
    """API 契约的单数 resource_type → VersionManager / 事件层的复数资源类型。"""
    if resource_type == "storyboard":
        return "storyboards"
    return ASSET_SPECS[resource_type].bucket_key


def resolve_current_image_rel(
    project: dict[str, Any],
    resource_type: str,
    resource_id: str,
    script: dict[str, Any] | None = None,
) -> str | None:
    """解析资源当前图的项目相对路径（不校验文件存在，交由调用方 ``safe_exists`` 判定）。

    - 资产（character / scene / prop / product）：读 project.json 对应 bucket 的 sheet
      字段；资产不存在抛 ``KeyError``，sheet 未设置返回 ``None``。
    - storyboard：读剧本条目的 ``generated_assets.storyboard_image``（旧宫格项目可能
      指向 ``scene_{id}_first.png``），缺失时回退 canonical 路径
      ``storyboards/scene_{id}.png``；条目不存在抛 ``KeyError``。
    """
    if resource_type == "storyboard":
        items, id_field, _, _, _ = get_storyboard_items(script or {})
        resolved = find_storyboard_item(items, id_field, resource_id)
        if resolved is None:
            raise KeyError(f"segment not found: {resource_id}")
        assets = resolved[0].get("generated_assets") or {}
        pointer = assets.get("storyboard_image") if isinstance(assets, dict) else None
        if isinstance(pointer, str) and pointer:
            return pointer
        return resource_relative_path("storyboards", resource_id)

    spec = ASSET_SPECS[resource_type]
    bucket = project.get(spec.bucket_key)
    entry = bucket.get(resource_id) if isinstance(bucket, dict) else None
    if not isinstance(entry, dict):
        raise KeyError(f"{resource_type} not found: {resource_id}")
    sheet = entry.get(spec.sheet_field)
    return sheet if isinstance(sheet, str) and sheet else None


async def execute_image_edit_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """执行图片编辑任务：读 current 图 → i2i → 新版本覆盖 current → 按资源类型写回。

    编辑必然 i2i（唯一入队即知 capability 的图片任务，见 ``docs/adr/0001``），故
    ``needs_i2i=True`` 恒定走 i2i 槽；backend 调用失败时 current 图指针与资源写回
    不被触碰（MediaGenerator 仅在成功后覆盖 output 并登记新版本，写回也不会发生）。
    旧图基线登记（见下方 ``ensure_current_tracked`` 调用）先于 backend 调用发生，与
    backend 成败无关、失败时不回滚——保证编辑前的旧图始终可回滚，不属于本次生成
    的版本变更范畴。
    """
    resource_type = str(payload.get("resource_type") or "")
    if resource_type not in EDITABLE_RESOURCE_TYPES:
        raise ValueError(f"unsupported image_edit resource_type: {resource_type}")

    instruction = payload.get("prompt")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("instruction (payload.prompt) is required for image_edit task")
    instruction = instruction.strip()

    script_file = payload.get("script_file")
    if resource_type == "storyboard" and not script_file:
        raise ValueError("script_file is required for storyboard image_edit task")

    version_resource_type = edit_version_resource_type(resource_type)

    def _prepare():
        pm = get_project_manager()
        _project = pm.load_project(project_name)
        _project_path = pm.get_project_path(project_name)
        _script = pm.load_script(project_name, str(script_file)) if resource_type == "storyboard" else None
        _current_rel = resolve_current_image_rel(_project, resource_type, resource_id, _script)
        if not (_current_rel and safe_exists(_project_path, _current_rel)):
            raise ValueError(f"no current image to edit: {resource_type}/{resource_id}")
        return _project, _project_path / _current_rel

    project, current_image = await asyncio.to_thread(_prepare)

    generator = await get_media_generator(project_name, payload=payload, user_id=user_id, needs_i2i=True)

    # 旧图若尚无版本记录（如旧宫格项目 current 指向非 canonical 路径），先以中性元数据补登，
    # 保证编辑前的旧图可回滚；也避免 generate_image_async 内部的 ensure_current_tracked
    # 把编辑指令 / 编辑标记误写到旧版本上。已有版本记录时此调用是 no-op。
    await asyncio.to_thread(
        generator.versions.ensure_current_tracked,
        version_resource_type,
        resource_id,
        current_image,
        "",
    )

    aspect_ratio = get_aspect_ratio(project, version_resource_type)
    resolved_image = await _resolve_effective_image_backend(project, payload, needs_i2i=True)
    image_size = await _resolve_resolution(project, resolved_image.provider_id, resolved_image.model_id)

    # 参考图仅当前图一张、prompt 仅编辑指令（不拼原 image_prompt / 不追加生成路径的
    # 自动参考图收集）；新版本 prompt 字段即编辑指令，source 标记编辑版本。
    _, version = await generator.generate_image_async(
        prompt=instruction,
        resource_type=version_resource_type,
        resource_id=resource_id,
        reference_images=[current_image],
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        source=IMAGE_EDIT_VERSION_SOURCE,
    )

    canonical_rel = resource_relative_path(version_resource_type, resource_id)

    def _finalize():
        pm = get_project_manager()
        if resource_type == "storyboard":
            pm.update_scene_asset(
                project_name=project_name,
                script_filename=str(script_file),
                scene_id=resource_id,
                asset_type="storyboard_image",
                asset_path=canonical_rel,
            )
        else:
            pm._update_asset_sheet(resource_type, project_name, resource_id, canonical_rel)
        return generator.versions.get_versions(version_resource_type, resource_id)["versions"][-1]["created_at"]

    created_at = await asyncio.to_thread(_finalize)

    return {
        "version": version,
        "file_path": canonical_rel,
        "created_at": created_at,
        "resource_type": version_resource_type,
        "resource_id": resource_id,
    }
