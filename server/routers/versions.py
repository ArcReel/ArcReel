"""
版本管理 API 路由

处理版本查询和还原请求。
"""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter

logger = logging.getLogger(__name__)

from lib.api_errors import BadRequestError, ConflictError
from lib.artifact_activation import register_current_resource_artifact
from lib.async_thread import run_noninterruptible_sync
from lib.generation_admission import generation_admission_lock
from lib.path_safety import PathTraversalError, safe_join
from lib.project_change_hints import project_change_source
from lib.project_manager import get_project_manager
from lib.resource_paths import resource_relative_path
from lib.version_manager import VersionManager
from server.services.artifact_version_restore import (
    TypedMediaRestoreTarget,
    get_typed_media_restore_target,
    is_typed_media_restore_resource,
    is_typed_media_version_restorable,
    restore_typed_media_version,
)
from server.services.grid_access import ensure_grid_writable
from server.services.narration_delivery_tasks import active_narrated_video_resource_ids, active_tts_resource_ids
from server.services.presentation_read_model import is_presentation_version_available

router = APIRouter()

# 经此路由可还原的资源类型（API 面策略）。路径形状委托 lib.resource_paths，本路由
# 仅放行有还原后元数据同步分支的这几类。grids 的还原只换回联合图文件并复位宫格记录的
# 切分态，不触发切分、不碰任何分镜图——落格由宫格切分端点显式执行。
_RESTORABLE_RESOURCE_TYPES = frozenset(
    {"storyboards", "videos", "audio", "characters", "scenes", "props", "products", "reference_videos", "grids"}
)


def get_version_manager(project_name: str) -> VersionManager:
    """获取项目的版本管理器"""
    project_path = get_project_manager().get_project_path(project_name)
    return VersionManager(project_path)


def _resolve_resource_path(
    resource_type: str,
    resource_id: str,
    project_path: Path,
) -> tuple[Path, str]:
    """返回 (current_file_absolute, relative_file_path)；资源类型不可还原或 ID 越界时抛出领域异常。"""
    if resource_type not in _RESTORABLE_RESOURCE_TYPES:
        raise BadRequestError("unsupported_resource_type", resource_type=resource_type)
    relative = resource_relative_path(resource_type, resource_id)
    # 路径遍历防护：resource_id 拼出的绝对路径不得逃出项目目录（与 MediaGenerator._get_output_path 对齐）。
    try:
        current_file = safe_join(project_path, relative)
    except PathTraversalError as exc:
        raise BadRequestError("invalid_resource_id", resource_id=resource_id) from exc
    return current_file, relative


def _sync_grid_record(
    project_path: Path,
    resource_id: str,
    *,
    on_commit: Callable[[], None] | None = None,
) -> None:
    """还原联合图后复位宫格记录：内容已换回历史版本，旧的落格结果不再对应当前图。

    只动宫格记录自身（split_at / 失败态），不同步剧本或分镜——落格由切分端点显式执行；
    frame_chain 原样保留。记录缺失/损坏时跳过：联合图文件已还原成功，记录属 best-effort。

    还原本身不设在途闸门（与分镜图还原同口径），复位口径见
    ``GridGeneration.mark_composite_replaced``：生成在途时保留在途态，否则记录会谎报空闲。
    """
    from lib.grid_manager import GridManager

    manager = GridManager(project_path)
    updated = manager.update(
        resource_id,
        lambda grid: grid.mark_composite_replaced(),
        on_commit=on_commit,
        ignore_invalid=True,
    )
    if updated is None and on_commit is not None:
        on_commit()


# resource_type（复数，URL 段）→ asset_type（单数，ASSET_SPECS 键）
_RESOURCE_TO_ASSET_TYPE: dict[str, str] = {
    "characters": "character",
    "scenes": "scene",
    "props": "prop",
    "products": "product",
}


def _restore_non_typed_sidecars(
    *,
    resource_type: str,
    project_name: str,
    resource_id: str,
    file_path: str,
    project_path: Path,
) -> None:
    """Commit restore metadata and its Manifest claim under one compensation seam."""

    if resource_type == "storyboards":
        scripts_dir = project_path / "scripts"
        script_names = [path.name for path in sorted(scripts_dir.glob("*.json"))] if scripts_dir.exists() else []

        def _register_storyboard() -> None:
            register_current_resource_artifact(
                project_path,
                resource_type=resource_type,
                resource_id=resource_id,
            )

        with project_change_source("webui"):
            get_project_manager().update_scene_asset_across_scripts(
                project_name,
                script_names,
                resource_id,
                "storyboard_image",
                file_path,
                on_commit=_register_storyboard,
            )
        return

    asset_type = _RESOURCE_TO_ASSET_TYPE.get(resource_type)
    if asset_type is not None:

        def _register_asset(_project_file: Path) -> None:
            register_current_resource_artifact(
                project_path,
                resource_type=resource_type,
                resource_id=resource_id,
            )

        try:
            with project_change_source("webui"):
                get_project_manager()._update_asset_sheet(
                    asset_type,
                    project_name,
                    resource_id,
                    file_path,
                    on_commit=_register_asset,
                )
        except KeyError:
            _register_asset(project_path / "project.json")
        return

    if resource_type == "grids":

        def _register_grid() -> None:
            register_current_resource_artifact(
                project_path,
                resource_type=resource_type,
                resource_id=resource_id,
            )

        _sync_grid_record(project_path, resource_id, on_commit=_register_grid)
        return

    register_current_resource_artifact(
        project_path,
        resource_type=resource_type,
        resource_id=resource_id,
    )


# ==================== 版本查询 ====================


@router.get("/projects/{project_name}/versions/{resource_type}/{resource_id}")
async def get_versions(
    project_name: str,
    resource_type: str,
    resource_id: str,
):
    """
    获取资源的所有版本列表

    Args:
        project_name: 项目名称
        resource_type: 资源类型 (storyboards, videos, characters, scenes, props)
        resource_id: 资源 ID
    """
    try:

        def _sync():
            vm = get_version_manager(project_name)
            versions_info = vm.get_versions(resource_type, resource_id)
            for record in versions_info.get("versions", []):
                if isinstance(record, dict):
                    record["restorable"] = is_typed_media_version_restorable(resource_type, record)
                    record["presentation_available"] = is_presentation_version_available(resource_type, record)
            return {"resource_type": resource_type, "resource_id": resource_id, **versions_info}

        return await asyncio.to_thread(_sync)

    except ValueError as e:
        # 非法项目名 / 损坏的 versions.json 等坏请求，str(e) 可能含路径只进日志
        logger.warning("版本查询请求非法: %s", e)
        raise BadRequestError("request_invalid") from e


# ==================== 版本还原 ====================


@router.post("/projects/{project_name}/versions/{resource_type}/{resource_id}/restore/{version}")
async def restore_version(
    project_name: str,
    resource_type: str,
    resource_id: str,
    version: int,
):
    """
    切换到指定版本

    会将指定版本复制到当前路径，并把当前版本指针切换到该版本。

    Args:
        project_name: 项目名称
        resource_type: 资源类型
        resource_id: 资源 ID
        version: 要还原的版本号
    """
    try:
        target: TypedMediaRestoreTarget | None = None
        if is_typed_media_restore_resource(resource_type):
            target = await asyncio.to_thread(
                get_typed_media_restore_target,
                get_version_manager(project_name),
                resource_type=resource_type,
                resource_id=resource_id,
                version=version,
            )

        def _sync():
            # 还原同样是一条联合图写入路径（换回历史联合图 + 复位宫格记录），
            # 与重生成/切分/上传共用准入判定；漏掉这里，被封禁项目就能从还原绕过。
            if resource_type == "grids":
                ensure_grid_writable(get_project_manager().load_project(project_name))

            vm = get_version_manager(project_name)
            project_path = get_project_manager().get_project_path(project_name)
            current_file, file_path = _resolve_resource_path(resource_type, resource_id, project_path)

            if is_typed_media_restore_resource(resource_type):
                result = restore_typed_media_version(
                    project_manager=get_project_manager(),
                    project_name=project_name,
                    project_path=project_path,
                    versions=vm,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    version=version,
                    current_file=current_file,
                    artifact_path=file_path,
                )
            else:
                result = vm.restore_version(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    version=version,
                    current_file=current_file,
                    on_restore=lambda _record: _restore_non_typed_sidecars(
                        resource_type=resource_type,
                        project_name=project_name,
                        resource_id=resource_id,
                        file_path=file_path,
                        project_path=project_path,
                    ),
                )

            # 计算还原后文件的 fingerprint；视频还原时同步删除缩略图（内容已失效）
            asset_fingerprints: dict[str, int] = {}
            if current_file.exists():
                asset_fingerprints[file_path] = current_file.stat().st_mtime_ns

            if resource_type == "videos":
                thumbnail_path = project_path / "thumbnails" / f"scene_{resource_id}.jpg"
                thumbnail_key = f"thumbnails/scene_{resource_id}.jpg"
                thumbnail_path.unlink(missing_ok=True)
                # fingerprint=0 通知前端该文件已失效（poster 消失直到重新生成）
                asset_fingerprints[thumbnail_key] = 0
            elif resource_type == "reference_videos":
                thumbnail_path = project_path / "reference_videos" / "thumbnails" / f"{resource_id}.jpg"
                thumbnail_key = f"reference_videos/thumbnails/{resource_id}.jpg"
                thumbnail_path.unlink(missing_ok=True)
                asset_fingerprints[thumbnail_key] = 0

            return {
                "success": True,
                **result,
                "file_path": file_path,
                "asset_fingerprints": asset_fingerprints,
            }

        if target is not None:
            async with generation_admission_lock(
                project_name=project_name,
                script_file=target.script_file,
                resource_id=resource_id,
            ):
                if resource_type == "audio":
                    active_tts, active_video = await asyncio.gather(
                        active_tts_resource_ids(
                            project_name=project_name,
                            resource_ids=(resource_id,),
                            script_file=target.script_file,
                        ),
                        active_narrated_video_resource_ids(
                            project_name=project_name,
                            resource_ids=(resource_id,),
                            script_file=target.script_file,
                        ),
                    )
                    if resource_id in active_tts or resource_id in active_video:
                        raise ConflictError("audio_restore_conflicts_with_active_task", resource_id=resource_id)
                return await run_noninterruptible_sync(_sync)
        return await asyncio.to_thread(_sync)

    except ValueError as e:
        # 非法项目名 / 越界 resource_id 等坏请求，str(e) 可能含路径只进日志
        logger.warning("版本还原请求非法: %s", e)
        raise BadRequestError("request_invalid") from e
