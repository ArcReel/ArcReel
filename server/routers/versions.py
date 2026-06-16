"""
版本管理 API 路由

处理版本查询和还原请求。
"""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

from lib.app_data_dir import app_data_dir
from lib.i18n import Translator
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from lib.resource_paths import resource_relative_path
from lib.script_editor import ScriptEditError
from lib.version_manager import VersionManager
from server.auth import CurrentUser
from server.services.reference_video_tasks import apply_unit_video_assets

router = APIRouter()

# 初始化项目管理器
pm = ProjectManager(app_data_dir())

# 经此路由可还原的资源类型（API 面策略）。路径形状委托 lib.resource_paths，但本路由
# 仅放行有还原后元数据同步分支的这几类；grids 的还原是独立议题。
_RESTORABLE_RESOURCE_TYPES = frozenset(
    {"storyboards", "videos", "characters", "scenes", "props", "products", "reference_videos"}
)


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
    _t: Callable[..., str],
) -> tuple[Path, str]:
    """返回 (current_file_absolute, relative_file_path)；资源类型不可还原或 ID 越界时抛出 HTTPException。"""
    if resource_type not in _RESTORABLE_RESOURCE_TYPES:
        raise HTTPException(status_code=400, detail=_t("unsupported_resource_type", resource_type=resource_type))
    relative = resource_relative_path(resource_type, resource_id)
    current_file = project_path / relative
    # 路径遍历防护：resource_id 拼出的绝对路径不得逃出项目目录（与 MediaGenerator._get_output_path 对齐）。
    try:
        current_file.resolve().relative_to(project_path.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail=_t("invalid_resource_id", resource_id=resource_id))
    return current_file, relative


def _sync_scripts_best_effort(project_path: Path, apply: Callable[[str], None]) -> None:
    """对项目内每集剧本执行 apply（入参为脚本文件名），逐集降级而非整体失败。

    - KeyError：该集脚本不引用此资源，跳过同步是正常情况而非脏数据。
    - ScriptEditError：脏脚本（结构键损坏）降级跳过，warning 标出集名 + 原因。
    - OSError：transient IO 错误（单文件权限 / EBUSY / flock 超时 / 损坏 inode 等）。
      跨集同步是 best-effort housekeeping，主集恢复在调用本函数前已成功，不应让
      sibling 集的临时 IO 失败把整个 restore 操作 5xx。真正未预期的异常
      （RuntimeError / ImportError / ...）仍让它冒到 router 5xx 暴露。
    """
    scripts_dir = project_path / "scripts"
    if not scripts_dir.exists():
        return
    for script_file in scripts_dir.glob("*.json"):
        try:
            with project_change_source("webui"):
                apply(script_file.name)
        except KeyError:
            continue
        except ScriptEditError as exc:
            logger.warning("跨集同步元数据跳过脏脚本 %s: %s", script_file.name, exc)
            continue
        except OSError as exc:
            logger.warning("跨集同步元数据 sibling 集 %s IO 失败: %s", script_file.name, exc)
            continue


def _sync_storyboard_metadata(
    project_name: str,
    resource_id: str,
    file_path: str,
    project_path: Path,
) -> None:
    def _apply(script_name: str) -> None:
        get_project_manager().update_scene_asset(
            project_name=project_name,
            script_filename=script_name,
            scene_id=resource_id,
            asset_type="storyboard_image",
            asset_path=file_path,
        )

    _sync_scripts_best_effort(project_path, _apply)


def _sync_video_metadata(
    project_name: str,
    resource_id: str,
    file_path: str,
    project_path: Path,
) -> None:
    """还原镜头视频后同步 generated_assets。

    还原的是历史本地文件，旧 provider URI / 缩略图与之不再对应，一并清空
    （缩略图文件本身由 restore 端点删除，同步路径无法内联 async ffmpeg 重新抽帧）。
    """

    def _apply(script_name: str) -> None:
        get_project_manager().batch_update_scene_assets(
            project_name=project_name,
            script_filename=script_name,
            updates=[
                (resource_id, "video_clip", file_path),
                (resource_id, "video_uri", None),
                (resource_id, "video_thumbnail", None),
            ],
        )

    _sync_scripts_best_effort(project_path, _apply)


def _sync_reference_video_metadata(
    project_name: str,
    resource_id: str,
    project_path: Path,
) -> None:
    """还原参考视频单元后同步 unit.generated_assets（写回口径与生成 finalize 共用）。

    还原的是历史本地文件，旧 provider URI / 缩略图与之不再对应，一并清空；
    缩略图文件本身由 restore 端点删除（同步路径无法内联 async ffmpeg 重新抽帧）。
    """

    def _apply(script_name: str) -> None:
        # 资产回写热路径：只动 unit.generated_assets，豁免结构校验（与 update_scene_asset 对齐）。
        # 该集脚本不含此 unit 时 apply_unit_video_assets 抛 KeyError，锁内冒出即跳过写回。
        with get_project_manager().locked_script(project_name, script_name, validate=False) as script:
            apply_unit_video_assets(script, resource_id, video_uri=None, thumb_rel=None)

    _sync_scripts_best_effort(project_path, _apply)


# resource_type（复数，URL 段）→ asset_type（单数，ASSET_SPECS 键）
_RESOURCE_TO_ASSET_TYPE: dict[str, str] = {
    "characters": "character",
    "scenes": "scene",
    "props": "prop",
    "products": "product",
}


def _sync_metadata(
    resource_type: str,
    project_name: str,
    resource_id: str,
    file_path: str,
    project_path: Path,
) -> None:
    """还原后同步元数据，确保引用指向统一文件路径。"""
    asset_type = _RESOURCE_TO_ASSET_TYPE.get(resource_type)
    if asset_type is not None:
        try:
            with project_change_source("webui"):
                get_project_manager()._update_asset_sheet(asset_type, project_name, resource_id, file_path)
        except KeyError:
            pass  # 资产条目可能已从 project.json 删除，跳过元数据同步
    elif resource_type == "storyboards":
        _sync_storyboard_metadata(project_name, resource_id, file_path, project_path)
    elif resource_type == "videos":
        _sync_video_metadata(project_name, resource_id, file_path, project_path)
    elif resource_type == "reference_videos":
        _sync_reference_video_metadata(project_name, resource_id, project_path)


# ==================== 版本查询 ====================


@router.get("/projects/{project_name}/versions/{resource_type}/{resource_id}")
async def get_versions(
    project_name: str,
    resource_type: str,
    resource_id: str,
    _user: CurrentUser,
    _t: Translator,
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
            return {"resource_type": resource_type, "resource_id": resource_id, **versions_info}

        return await asyncio.to_thread(_sync)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


# ==================== 版本还原 ====================


@router.post("/projects/{project_name}/versions/{resource_type}/{resource_id}/restore/{version}")
async def restore_version(
    project_name: str,
    resource_type: str,
    resource_id: str,
    version: int,
    _user: CurrentUser,
    _t: Translator,
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

        def _sync():
            vm = get_version_manager(project_name)
            project_path = get_project_manager().get_project_path(project_name)
            current_file, file_path = _resolve_resource_path(resource_type, resource_id, project_path, _t)

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

        return await asyncio.to_thread(_sync)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))
