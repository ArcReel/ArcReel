"""镜头级分镜图/视频自主上传路由。

与通用资产上传（files.py）分离：镜头上传需要 script_file + shot_id 定位剧本条目、
纳入版本管理，并返回 asset_fingerprints 供上传方即时 cache-bust（SSE 兜底其他客户端）。

宫格模式无独立端点：拆分后的单元格图 canonical 路径与图生视频模式相同
（storyboards/scene_{id}.png），按镜头上传即覆盖该单元格，宫格记录不动。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile

from lib.app_data_dir import app_data_dir
from lib.i18n import Translator
from lib.image_utils import normalize_storyboard_upload
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from lib.resource_paths import resource_relative_path
from lib.script_editor import ScriptEditError
from lib.storyboard_sequence import find_storyboard_item, get_storyboard_items
from lib.version_manager import VersionManager
from server.auth import CurrentUser
from server.services.generation_tasks import emit_generation_success_batch
from server.services.upload_finalize import (
    UploadTooLargeError,
    UploadValidationError,
    finalize_shot_storyboard_upload,
    finalize_shot_video_upload,
    record_upload_version,
    save_uploaded_bytes,
    save_uploaded_video_stream,
    validate_upload,
)

logger = logging.getLogger(__name__)

router = APIRouter()

pm = ProjectManager(app_data_dir())


def get_project_manager() -> ProjectManager:
    return pm


@router.post("/projects/{project_name}/shots/{shot_id}/upload/{kind}")
async def upload_shot_media(
    project_name: str,
    shot_id: str,
    kind: Literal["storyboard", "video"],
    script_file: str,
    _user: CurrentUser,
    _t: Translator,
    file: UploadFile = File(...),
):
    """上传分镜图或镜头视频，替换该镜头的 AI 生成资产。

    与生成链路保持一致：旧文件补登版本 → 写 canonical 路径 → 登记新版本 →
    剧本元数据回写（status 自动推导）→ SSE batch 推送。
    """
    try:
        max_bytes = validate_upload(file.filename, file.size, kind="image" if kind == "storyboard" else "video")

        resource_type = "storyboards" if kind == "storyboard" else "videos"
        relative_path = resource_relative_path(resource_type, shot_id)

        def _validate_shot() -> tuple[Path, VersionManager]:
            project_path = get_project_manager().get_project_path(project_name)
            script = get_project_manager().load_script(project_name, script_file)
            # reference_video 剧本返回空列表 → 404，该模式的视频上传走 reference-videos 路由
            items, id_field, _, _, _ = get_storyboard_items(script)
            if find_storyboard_item(items, id_field, shot_id) is None:
                raise HTTPException(status_code=404, detail=_t("segment_not_found", id=shot_id))
            # 路径遍历防护：shot_id 拼出的绝对路径不得逃出项目目录（与 versions.py 对齐）
            target = project_path / relative_path
            try:
                target.resolve().relative_to(project_path.resolve())
            except ValueError:
                raise HTTPException(status_code=400, detail=_t("invalid_resource_id", resource_id=shot_id))
            return project_path, VersionManager(project_path)

        project_path, versions = await asyncio.to_thread(_validate_shot)
        target = project_path / relative_path

        with project_change_source("webui"):
            # 旧文件若从未入版本库（如历史迁移），先补登，避免被覆盖后字节丢失
            await asyncio.to_thread(versions.ensure_current_tracked, resource_type, shot_id, target, "")

            if kind == "storyboard":
                # 限定读入内存的字节数：Content-Length 缺失/被绕过时不至于 OOM
                content = await file.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise UploadTooLargeError(max_bytes)
                try:
                    png_bytes = await asyncio.to_thread(normalize_storyboard_upload, content)
                except ValueError:
                    raise HTTPException(status_code=400, detail=_t("invalid_image_file"))
                await save_uploaded_bytes(png_bytes, target)
            else:
                await save_uploaded_video_stream(file.file, target, max_bytes=max_bytes)

            version = await asyncio.to_thread(
                record_upload_version,
                versions=versions,
                resource_type=resource_type,
                resource_id=shot_id,
                current_file=target,
                original_filename=file.filename,
            )

            if kind == "storyboard":
                await finalize_shot_storyboard_upload(
                    project_name=project_name, script_file=script_file, shot_id=shot_id, asset_path=relative_path
                )
            else:
                await finalize_shot_video_upload(
                    project_name=project_name,
                    script_file=script_file,
                    shot_id=shot_id,
                    project_path=project_path,
                    video_rel=relative_path,
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
        raise HTTPException(status_code=e.status_code, detail=_t(e.key, **e.params))
    except FileNotFoundError:
        # 不回传 str(e)：load_script 的异常信息含服务器绝对路径
        raise HTTPException(status_code=404, detail=_t("script_not_found", name=script_file))
    except KeyError:
        raise HTTPException(status_code=404, detail=_t("segment_not_found", id=shot_id))
    except ScriptEditError as e:
        raise HTTPException(status_code=400, detail=_t("script_data_corrupted", reason=str(e)))
    except HTTPException:
        raise
    except Exception as e:
        # 不回传 str(e)：未预期异常的消息可能含服务器路径等内部细节，堆栈进日志即可
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from e
