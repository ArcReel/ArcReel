"""
版本管理 API 路由

处理版本查询和还原请求。
"""

import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

from lib import PROJECT_ROOT
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from lib.version_manager import VersionManager
from server.auth import CurrentUser

router = APIRouter()

# 初始化项目管理器
pm = ProjectManager(PROJECT_ROOT / "projects")

_RESOURCE_FILE_PATTERNS: dict[str, tuple[str, str]] = {
    "storyboards": ("storyboards", "scene_{id}.png"),
    "videos":      ("videos",      "scene_{id}.mp4"),
    "characters":  ("characters",  "{id}.png"),
    "clues":       ("clues",       "{id}.png"),
}


def get_project_manager() -> ProjectManager:
    return pm


def get_version_manager(project_name: str) -> VersionManager:
    """获取项目的版本管理器"""
    project_path = get_project_manager().get_project_path(project_name)
    return VersionManager(project_path)


def _resolve_resource_path(
    resource_type: str,
    resource_id: str,
    project_path: Path,
) -> tuple[Path, str]:
    """返回 (current_file_absolute, relative_file_path)，资源类型无效时抛出 HTTPException。"""
    pattern = _RESOURCE_FILE_PATTERNS.get(resource_type)
    if pattern is None:
        raise HTTPException(status_code=400, detail=f"不支持的资源类型: {resource_type}")
    subdir, name_tpl = pattern
    name = name_tpl.format(id=resource_id)
    return project_path / subdir / name, f"{subdir}/{name}"


def _sync_storyboard_metadata(
    project_name: str,
    resource_id: str,
    file_path: str,
    project_path: Path,
) -> None:
    scripts_dir = project_path / "scripts"
    if not scripts_dir.exists():
        return
    for script_file in scripts_dir.glob("*.json"):
        try:
            with project_change_source("webui"):
                get_project_manager().update_scene_asset(
                    project_name=project_name,
                    script_filename=script_file.name,
                    scene_id=resource_id,
                    asset_type="storyboard_image",
                    asset_path=file_path,
                )
        except KeyError:
            continue
        except Exception as exc:
            logger.warning("同步分镜元数据失败: %s", exc)
            continue


def _sync_metadata(
    resource_type: str,
    project_name: str,
    resource_id: str,
    file_path: str,
    project_path: Path,
) -> None:
    """还原后同步元数据，确保引用指向统一文件路径。"""
    if resource_type == "characters":
        try:
            with project_change_source("webui"):
                get_project_manager().update_project_character_sheet(project_name, resource_id, file_path)
        except KeyError:
            pass  # 角色条目可能已从 project.json 删除，跳过元数据同步
    elif resource_type == "clues":
        try:
            with project_change_source("webui"):
                get_project_manager().update_clue_sheet(project_name, resource_id, file_path)
        except KeyError:
            pass  # 线索条目可能已从 project.json 删除，跳过元数据同步
    elif resource_type == "storyboards":
        _sync_storyboard_metadata(project_name, resource_id, file_path, project_path)


# ==================== 版本查询 ====================

@router.get("/projects/{project_name}/versions/{resource_type}/{resource_id}")
async def get_versions(
    project_name: str,
    resource_type: str,
    resource_id: str,
    _user: CurrentUser,
):
    """
    获取资源的所有版本列表

    Args:
        project_name: 项目名称
        resource_type: 资源类型 (storyboards, videos, characters, clues)
        resource_id: 资源 ID
    """
    try:
        vm = get_version_manager(project_name)
        versions_info = vm.get_versions(resource_type, resource_id)

        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            **versions_info
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 版本还原 ====================

@router.post("/projects/{project_name}/versions/{resource_type}/{resource_id}/restore/{version}")
async def restore_version(
    project_name: str,
    resource_type: str,
    resource_id: str,
    version: int,
    _user: CurrentUser,
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
        vm = get_version_manager(project_name)
        project_path = get_project_manager().get_project_path(project_name)
        current_file, file_path = _resolve_resource_path(resource_type, resource_id, project_path)

        result = vm.restore_version(
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
            current_file=current_file,
        )

        _sync_metadata(resource_type, project_name, resource_id, file_path, project_path)

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

        return {
            "success": True,
            **result,
            "file_path": file_path,
            "asset_fingerprints": asset_fingerprints,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
