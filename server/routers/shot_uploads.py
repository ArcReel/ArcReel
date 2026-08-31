"""分镜级分镜图/视频自主上传路由。

与通用资产上传（files.py）分离，不是 UPLOAD_SPECS 的表项：本路由按 script_file + shot_id
定位剧本条目、纳入版本管理（VersionManager）、经 finalize_shot_* 回写剧本元数据，并返回
asset_fingerprints 供上传方即时 cache-bust（SSE 兜底其他客户端）——UploadSpec 的
subdir + naming 路径模型与 MetadataSetter 签名不覆盖这条管线。扩展名与大小校验共用
upload_finalize.validate_upload。

宫格切分后的单元格图 canonical 路径与分镜图生视频相同（storyboards/scene_{id}.png），
按分镜上传即覆盖该单元格，宫格记录不动；联合图整图上传走 grids.py 的独立端点。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile

from lib.api_errors import ApiError, NotFoundError
from lib.i18n import Translator
from lib.image_utils import normalize_storyboard_upload
from lib.path_safety import PathTraversalError, safe_join
from lib.project_change_hints import project_change_source
from lib.project_manager import get_project_manager
from lib.resource_paths import resource_relative_path
from lib.script_editor import ScriptEditError
from lib.storyboard_sequence import find_storyboard_item, get_storyboard_items
from lib.version_manager import VersionManager
from server.error_handlers import script_edit_detail
from server.services.generation_tasks import emit_generation_success_batch
from server.services.upload_finalize import (
    UploadTooLargeError,
    UploadValidationError,
    commit_manual_storyboard_upload,
    finalize_shot_video_upload,
    stage_uploaded_bytes,
    stage_uploaded_video_stream,
    validate_upload,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/projects/{project_name}/shots/{shot_id}/upload/{kind}")
async def upload_shot_media(
    project_name: str,
    shot_id: str,
    kind: Literal["storyboard", "video"],
    script_file: str,
    _t: Translator,
    file: UploadFile = File(...),
):
    """上传分镜图或分镜视频，替换该分镜的 AI 生成资产。

    与生成链路保持一致：staging → 正式选择与元数据原子提交（status 自动推导）→
    SSE batch 推送。
    """
    try:
        max_bytes = validate_upload(file.filename, file.size, kind="image" if kind == "storyboard" else "video")

        resource_type = "storyboards" if kind == "storyboard" else "videos"
        relative_path = resource_relative_path(resource_type, shot_id)

        def _validate_shot() -> tuple[Path, VersionManager]:
            # 项目缺失单独映射：落到外层 except FileNotFoundError 会误报「剧本不存在」
            try:
                project_path = get_project_manager().get_project_path(project_name)
            except FileNotFoundError as exc:
                raise NotFoundError("project_not_found", name=project_name) from exc
            script = get_project_manager().load_script(project_name, script_file)
            # reference_video 剧本返回空列表 → 404，该模式的视频上传走 reference-videos 路由
            items, id_field, _, _, _ = get_storyboard_items(script)
            if find_storyboard_item(items, id_field, shot_id) is None:
                raise HTTPException(status_code=404, detail=_t("segment_not_found", id=shot_id))
            # 路径遍历防护：shot_id 拼出的绝对路径不得逃出项目目录（与 versions.py 对齐）
            try:
                safe_join(project_path, relative_path)
            except PathTraversalError as exc:
                raise HTTPException(status_code=400, detail=_t("invalid_resource_id", resource_id=shot_id)) from exc
            return project_path, VersionManager(project_path)

        project_path, versions = await asyncio.to_thread(_validate_shot)
        target = project_path / relative_path

        with project_change_source("webui"):
            if kind == "storyboard":
                # 限定读入内存的字节数：Content-Length 缺失/被绕过时不至于 OOM
                content = await file.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise UploadTooLargeError(max_bytes)
                try:
                    png_bytes = await asyncio.to_thread(normalize_storyboard_upload, content)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=_t("invalid_image_file")) from exc
                staged_image = await asyncio.to_thread(stage_uploaded_bytes, png_bytes, target)
                version = await commit_manual_storyboard_upload(
                    project_name=project_name,
                    script_file=script_file,
                    shot_id=shot_id,
                    asset_path=relative_path,
                    staged_image=staged_image,
                    current_image=target,
                    versions=versions,
                    original_filename=file.filename,
                )
            else:
                staged_video = await stage_uploaded_video_stream(file.file, target, max_bytes=max_bytes)
                version = await finalize_shot_video_upload(
                    project_name=project_name,
                    script_file=script_file,
                    shot_id=shot_id,
                    project_path=project_path,
                    video_rel=relative_path,
                    staged_video=staged_video,
                    versions=versions,
                    original_filename=file.filename,
                )

            # emit 内部会读剧本解析 episode 并计算指纹，放线程池避免阻塞事件循环；
            # 返回的指纹直接复用进响应体，免二次计算
            fingerprints = await asyncio.to_thread(
                emit_generation_success_batch,
                task_type=kind,
                project_name=project_name,
                resource_id=shot_id,
                payload={"script_file": script_file},
            )

        return {
            "success": True,
            "path": relative_path,
            "version": version,
            "asset_fingerprints": fingerprints,
        }

    except UploadValidationError as e:
        raise HTTPException(status_code=e.status_code, detail=_t(e.key, **e.params)) from e
    except FileNotFoundError as exc:
        # 不回传 str(exc)：load_script 的异常信息含服务器绝对路径
        raise NotFoundError("script_not_found", name=script_file) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_t("segment_not_found", id=shot_id)) from exc
    except ScriptEditError as e:
        raise HTTPException(status_code=400, detail=script_edit_detail(e, _t)) from e
    except (HTTPException, ApiError):
        raise
    except Exception as e:
        # 不回传 str(e)：未预期异常的消息可能含服务器路径等内部细节，堆栈进日志即可
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from e
