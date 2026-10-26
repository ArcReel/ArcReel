"""
版本管理 API 路由

处理版本查询和还原请求。
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

from lib.artifacts.version_manager import VersionManager
from lib.infra.api_errors import BadRequestError
from lib.infra.async_thread import run_sync_transaction
from lib.project.asset_derivatives import derivative_artifact_id
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from lib.script.grid.grid_access import ensure_grid_writable
from server.services.currency.artifact_version_restore import (
    is_typed_media_restore_resource,
    is_typed_media_version_restorable,
)
from server.services.currency.image_version_restore import restore_non_typed_version
from server.services.currency.typed_media_restore import resolve_media_file, restore_typed_media_resource
from server.services.presentation.presentation_read_model import is_presentation_version_available

router = APIRouter()

# 经此路由可还原的资源类型（API 面策略）。路径形状委托 lib.project.resource_paths，本路由
# 仅放行有还原后元数据同步分支的这几类。grids 的还原只换回联合图文件并复位宫格记录的
# 切分态，不触发切分、不碰任何分镜图——落格由宫格切分端点显式执行。
_RESTORABLE_RESOURCE_TYPES = frozenset(
    {
        "storyboards",
        "videos",
        "audio",
        "characters",
        "scenes",
        "props",
        "products",
        "reference_videos",
        "grids",
        CHARACTER_DERIVATIVE_RESOURCE_TYPE,
    }
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
    return resolve_media_file(resource_type, resource_id, project_path)


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
    return await read_resource_versions(project_name, resource_type, resource_id)


@router.get("/projects/{project_name}/versions/character-derivative/{owner_name}/{derivative_name}")
async def get_derivative_versions(
    project_name: str,
    owner_name: str,
    derivative_name: str,
):
    """获取一个角色衍生资产图的版本列表。

    衍生的资源 id 是 ``本体名/衍生名``，塞不进通用版本路由的单段路径参数，故单列两段路径。
    """
    return await read_resource_versions(
        project_name,
        CHARACTER_DERIVATIVE_RESOURCE_TYPE,
        derivative_artifact_id(owner_name, derivative_name),
    )


@router.post("/projects/{project_name}/versions/character-derivative/{owner_name}/{derivative_name}/restore/{version}")
async def restore_derivative_version(
    project_name: str,
    owner_name: str,
    derivative_name: str,
    version: int,
):
    """把一个角色衍生资产图切回指定历史版本。"""
    return await restore_resource_version(
        project_name,
        CHARACTER_DERIVATIVE_RESOURCE_TYPE,
        derivative_artifact_id(owner_name, derivative_name),
        version,
    )


async def read_resource_versions(project_name: str, resource_type: str, resource_id: str) -> dict[str, Any]:
    """版本列表的读取实现。资源 id 带层级（角色衍生）的路由不能走单段路径参数，共用此函数。"""
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
    return await restore_resource_version(project_name, resource_type, resource_id, version)


async def restore_resource_version(
    project_name: str,
    resource_type: str,
    resource_id: str,
    version: int,
) -> dict[str, Any]:
    """版本还原的实现。资源 id 带层级（角色衍生）的路由不能走单段路径参数，共用此函数。"""
    try:
        if is_typed_media_restore_resource(resource_type):
            with project_change_source("webui"):
                return await restore_typed_media_resource(
                    project_manager=get_project_manager(),
                    versions=get_version_manager(project_name),
                    project_name=project_name,
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

            result = restore_non_typed_version(
                versions=vm,
                resource_type=resource_type,
                project_name=project_name,
                resource_id=resource_id,
                version=version,
                current_file=current_file,
                file_path=file_path,
                project_path=project_path,
                project_manager=get_project_manager(),
            )

            # 计算还原后文件的 fingerprint
            asset_fingerprints: dict[str, int] = {}
            if current_file.exists():
                asset_fingerprints[file_path] = current_file.stat().st_mtime_ns

            return {
                "success": True,
                **result,
                "file_path": file_path,
                "asset_fingerprints": asset_fingerprints,
            }

        return await run_sync_transaction(_sync)

    except ValueError as e:
        # 非法项目名 / 越界 resource_id 等坏请求，str(e) 可能含路径只进日志
        logger.warning("版本还原请求非法: %s", e)
        raise BadRequestError("request_invalid") from e
