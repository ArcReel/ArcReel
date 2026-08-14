"""图片指令式编辑（image edit）任务执行层。

编辑语义（见 ``docs/adr/0050`` 与 CONTEXT.md「图片编辑」术语）：以资源的当前图为唯一
参考图、用户编辑指令为唯一 prompt 调用 i2i，产出新图覆盖 current 并自动进版本历史；
原 image_prompt 不回写——编辑是对**图**的分叉而非对 prompt 的分叉。

支持的资源：character / scene / prop / product 四类设计图 + storyboard 分镜图。
「当前图路径解析」与「按资源类型写回」复用生成链路的既有口径（资产 sheet 字段 /
剧本 generated_assets），由 :func:`resolve_current_image_rel` 统一提供给路由
（入队前校验）与 executor（执行时读取），两侧口径不分叉。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    resolve_artifact_episode,
)
from lib.artifact_manifest import ArtifactBasis, ArtifactKey
from lib.asset_types import ASSET_SPECS, resolve_asset_key
from lib.async_thread import run_noninterruptible_sync
from lib.db.base import DEFAULT_USER_ID
from lib.image_reference_snapshot import freeze_image_references
from lib.path_safety import safe_exists
from lib.resource_paths import resource_relative_path
from lib.script_models import get_generated_assets
from lib.storyboard_sequence import find_storyboard_item, get_storyboard_items
from lib.visual_artifact_provenance import (
    VisualReference,
    build_asset_sheet_visual_basis,
    build_storyboard_image_visual_basis,
)
from server.services.generation_context import ImageLaneRequest, resolve_generation_context
from server.services.generation_tasks import (
    _assert_formal_image_reference_claims_usable,
    _asset_sheet_formal_image_callback,
    _finalize_asset_sheet_task,
    _finalize_storyboard_image_task,
    _formal_image_reference_is_usable,
    _formal_image_task_token,
    _FormalImageReferenceClaim,
    _storyboard_formal_image_callback,
    compensable_formal_task_result,
    get_aspect_ratio,
    get_project_manager,
)

# 版本记录里标记「指令式编辑」的 source 值；前端据此展示编辑标记（与 manual_upload 同机制）
IMAGE_EDIT_VERSION_SOURCE = "image_edit"

# 可编辑的资源类型白名单（API 契约用的单数形态；storyboard 之外与 ASSET_SPECS 同源）
EDITABLE_RESOURCE_TYPES: tuple[str, ...] = (*ASSET_SPECS.keys(), "storyboard")


@dataclass(frozen=True, slots=True)
class _ImageEditSource:
    """One exact current image admitted for editing and its recheck evidence."""

    resource_id: str
    artifact_path: str
    formal_claims: tuple[_FormalImageReferenceClaim, ...]


def _build_image_edit_basis(
    *,
    resource_type: str,
    resource_id: str,
    instruction: str,
    aspect_ratio: str,
    source: VisualReference,
) -> ArtifactBasis:
    """Describe the prompt and sole image input actually used by image editing."""

    if resource_type == "storyboard":
        return build_storyboard_image_visual_basis(
            resource_id=resource_id,
            image_prompt=instruction,
            # Editing sends only the instruction; project style is not a provider input.
            style="",
            aspect_ratio=aspect_ratio,
            references=(source,),
        )
    return build_asset_sheet_visual_basis(
        asset_type=resource_type,
        asset_id=resource_id,
        description=instruction,
        # Asset editing likewise omits the normal generation style expansion.
        style="",
        style_description="",
        aspect_ratio=aspect_ratio,
        references=(source,),
    )


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
      指向 ``scene_{id}_first.png``）；Manifest 激活前缺失时回退 canonical 路径，
      激活后不再按同名文件推断 current；条目不存在抛 ``KeyError``。
    """
    return _resolve_current_image_pointer(project, resource_type, resource_id, script)[1]


def _resolve_current_image_pointer(
    project: dict[str, Any],
    resource_type: str,
    resource_id: str,
    script: dict[str, Any] | None,
) -> tuple[str, str | None]:
    """Resolve the canonical resource identity and its metadata-owned image pointer."""

    if resource_type == "storyboard":
        items, id_field, _, _, _ = get_storyboard_items(script or {})
        resolved = find_storyboard_item(items, id_field, resource_id)
        if resolved is None:
            raise KeyError(f"segment not found: {resource_id}")
        item = resolved[0]
        resolved_id = str(item.get(id_field) or resource_id)
        pointer = get_generated_assets(item).get("storyboard_image")
        if isinstance(pointer, str) and pointer:
            return resolved_id, pointer
        if project.get("schema_version") == 8:
            return resolved_id, None
        return resolved_id, resource_relative_path("storyboards", resolved_id)

    spec = ASSET_SPECS[resource_type]
    bucket = project.get(spec.bucket_key)
    # 请求里的资产名与桶 key 可能是 NFC/NFD 中的任一形态，按坐标系解析真实落盘 key
    key = resolve_asset_key(bucket, resource_id)
    entry = bucket.get(key) if isinstance(bucket, dict) and key is not None else None
    if not isinstance(entry, dict):
        raise KeyError(f"{resource_type} not found: {resource_id}")
    sheet = entry.get(spec.sheet_field)
    return str(key), sheet if isinstance(sheet, str) and sheet else None


def resolve_usable_image_edit_source(
    *,
    project: dict[str, Any],
    project_path: Path,
    resource_type: str,
    resource_id: str,
    script: dict[str, Any] | None,
    artifact_episode: int | None,
    resolver: ArtifactCurrencyResolver | None,
) -> _ImageEditSource | None:
    """Select one metadata-owned current image through the active Manifest boundary.

    Before activation, file existence preserves the legacy admission rule. Once
    active, the exact asset-sheet or episode-storyboard claim is authoritative;
    missing claims are not selectable and blocked comparisons fail loud. The
    returned claim is retained so the executor can recheck it after async provider
    resolution and immediately before provider submission.
    """

    resolved_id, artifact_path = _resolve_current_image_pointer(project, resource_type, resource_id, script)
    if not artifact_path:
        return None
    if resolver is None:
        if not safe_exists(project_path, artifact_path):
            return None
        return _ImageEditSource(resource_id=resolved_id, artifact_path=artifact_path, formal_claims=())

    if resource_type == "storyboard":
        if type(artifact_episode) is not int or artifact_episode < 1:
            raise ValueError("artifact_episode is required for an active storyboard edit source")
        key = ArtifactKey.episode_storyboard(artifact_episode, resolved_id)
    else:
        key = ArtifactKey.asset_sheet(resource_type, resolved_id)
    claims: list[_FormalImageReferenceClaim] = []
    if not _formal_image_reference_is_usable(
        resolver=resolver,
        key=key,
        artifact_path=artifact_path,
        claims=claims,
    ):
        return None
    return _ImageEditSource(resource_id=resolved_id, artifact_path=artifact_path, formal_claims=tuple(claims))


async def execute_image_edit_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """执行图片编辑任务：读 current 图 → i2i → 新版本覆盖 current → 按资源类型写回。

    编辑必然 i2i（唯一入队即知 capability 的图片任务，见 ``docs/adr/0001``），故声明
    ``ImageLaneRequest(capability="i2i")`` 恒定走 i2i 槽；backend 调用失败时 current 图指针与资源写回
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
        _artifact_episode = None
        if _script is not None:
            _artifact_episode = resolve_artifact_episode(
                project=_project,
                script=_script,
                script_filename=str(script_file),
            )
        _source = resolve_usable_image_edit_source(
            project=_project,
            project_path=_project_path,
            resource_type=resource_type,
            resource_id=resource_id,
            script=_script,
            artifact_episode=_artifact_episode,
            resolver=active_artifact_currency_resolver(_project_path, _project),
        )
        if _source is None:
            raise ValueError(f"no current image to edit: {resource_type}/{resource_id}")
        # 资产名可能以 NFC/NFD 任一形态传入：选择缝返回真实落盘 key，之后的版本登记与
        # canonical 图路径统一按它写，避免同一资产落出两种编码形式的文件与版本记录。
        _key = _source.resource_id
        _current_rel = _source.artifact_path
        _current_image = _project_path / _current_rel
        _aspect_ratio = get_aspect_ratio(_project, version_resource_type)
        _frozen = freeze_image_references(
            [_current_image],
            [
                VisualReference(
                    path=_current_image,
                    role="edit_source",
                    logical_type=resource_type,
                    logical_id=_key,
                    kind="current",
                )
            ],
        )
        try:
            _basis = _build_image_edit_basis(
                resource_type=resource_type,
                resource_id=_key,
                instruction=instruction,
                aspect_ratio=_aspect_ratio,
                source=_frozen.visual_references[0],
            )
        except BaseException:
            _frozen.cleanup()
            raise
        return _project, _project_path, _current_image, _key, _aspect_ratio, _frozen, _basis, _source.formal_claims

    (
        project,
        project_path,
        current_image,
        resource_key,
        aspect_ratio,
        frozen_references,
        edit_basis,
        formal_claims,
    ) = await asyncio.to_thread(_prepare)

    canonical_rel = resource_relative_path(version_resource_type, resource_key)
    formal_outcomes: list[Any] = []
    try:
        # 编辑必然 i2i：单次解析拿到 generator 与 image lane 产物（provider / backend / resolution）。
        ctx = await resolve_generation_context(
            project_name,
            payload,
            project=project,
            user_id=user_id,
            image=ImageLaneRequest(capability="i2i"),
        )
        generator = ctx.generator

        await asyncio.to_thread(
            _assert_formal_image_reference_claims_usable,
            project_path,
            project,
            formal_claims,
        )

        # 旧图若尚无版本记录（如旧宫格项目 current 指向非 canonical 路径），先以中性元数据补登，
        # 保证编辑前的旧图可回滚；也避免 generate_image_async 内部的 ensure_current_tracked
        # 把编辑指令 / 编辑标记误写到旧版本上。已有版本记录时此调用是 no-op。
        await asyncio.to_thread(
            generator.versions.ensure_current_tracked,
            version_resource_type,
            resource_key,
            current_image,
            "",
        )
        await asyncio.to_thread(
            _assert_formal_image_reference_claims_usable,
            project_path,
            project,
            formal_claims,
        )

        if resource_type == "storyboard":
            commit_formal_output = _storyboard_formal_image_callback(
                project_name=project_name,
                script_file=str(script_file),
                resource_id=resource_key,
                artifact_path=canonical_rel,
                prompt=instruction,
                versions=generator.versions,
                task_id=task_id,
                basis=edit_basis,
                outcome_box=formal_outcomes,
                project_manager=get_project_manager(),
            )
        else:
            commit_formal_output = _asset_sheet_formal_image_callback(
                asset_type=resource_type,
                project_name=project_name,
                resource_id=resource_key,
                sheet_path=canonical_rel,
                prompt=instruction,
                versions=generator.versions,
                task_id=task_id,
                basis=edit_basis,
                outcome_box=formal_outcomes,
                project_manager=get_project_manager(),
            )

        # 参考图仅当前图一张、prompt 仅编辑指令（不拼原 image_prompt / 不追加生成路径的
        # 自动参考图收集）；provider 与 frozen basis 共享 task-owned 源图字节。
        _, version = await generator.generate_image_async(
            prompt=instruction,
            resource_type=version_resource_type,
            resource_id=resource_key,
            reference_images=frozen_references.reference_images,
            aspect_ratio=aspect_ratio,
            image_size=ctx.image.resolution,
            formal_output=True,
            task_id=_formal_image_task_token(task_id),
            commit_formal_output=commit_formal_output,
            source=IMAGE_EDIT_VERSION_SOURCE,
        )
    finally:
        await run_noninterruptible_sync(frozen_references.cleanup)

    if formal_outcomes:
        outcome = formal_outcomes[0]
        version, created_at, receipt = outcome.version, outcome.created_at, outcome.receipt
    elif resource_type == "storyboard":
        created_at, receipt = await _finalize_storyboard_image_task(
            project_name=project_name,
            script_file=str(script_file),
            resource_id=resource_key,
            artifact_path=canonical_rel,
            generator=generator,
            version=version,
            task_id=task_id,
            basis=edit_basis,
            project_manager=get_project_manager(),
        )
    else:
        created_at, receipt = await _finalize_asset_sheet_task(
            asset_type=resource_type,
            project_name=project_name,
            resource_id=resource_key,
            sheet_path=canonical_rel,
            generator=generator,
            version=version,
            task_id=task_id,
            basis=edit_basis,
            project_manager=get_project_manager(),
        )

    return compensable_formal_task_result(
        {
            "version": version,
            "file_path": canonical_rel,
            "created_at": created_at,
            "resource_type": version_resource_type,
            "resource_id": resource_key,
        },
        receipt,
    )
