"""角色衍生资产图的上传替换路由（见 ``docs/adr/0072``）。

上传的图是成品：落到衍生的规范路径 ``characters/derivatives/{角色}/{衍生}.png``，作为一条选中的
手动上传版本进入版本历史，清单按图字节登记（``docs/adr/0062``），本体资产图与变化描述都不参与。
上传只替换资产图，不作为再编辑生成的底图；变化描述保持不变。

与本体资产图的上传（``files.py`` 的 ``UPLOAD_SPECS``）分开：衍生的坐标是「本体/衍生」两段，落盘
扩展名固定为 ``.png``，提交走衍生资产图生成与图片编辑共用的正式活化回调。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from lib.artifacts.version_manager import VersionManager
from lib.infra.api_errors import ApiError, NotFoundError
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.infra.image_utils import normalize_storyboard_upload
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project.asset_derivatives import DERIVATIVE_TASK_TYPE, DerivativeSheetTarget, resolve_derivative_target
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from server.i18n import Translator
from server.services.currency.upload_finalize import (
    UploadTooLargeError,
    UploadValidationError,
    commit_manual_derivative_upload,
    stage_uploaded_bytes,
    validate_upload,
)
from server.services.tasks.generation_tasks import emit_generation_success_batch

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/projects/{project_name}/characters/{char_name}/derivatives/{derivative_name}/upload")
async def upload_derivative_sheet(
    project_name: str,
    char_name: str,
    derivative_name: str,
    _t: Translator,
    file: UploadFile = File(...),
):
    """上传一张图替换角色衍生的资产图，成为该衍生的当前版本。

    不要求本体已有资产图：上传的图是成品，不以本体资产图为输入。
    """
    try:
        max_bytes = validate_upload(file.filename, file.size, kind="image")

        def _resolve() -> tuple[DerivativeSheetTarget, Path, VersionManager]:
            manager = get_project_manager()
            try:
                project_path = manager.get_project_path(project_name)
            except FileNotFoundError as exc:
                raise NotFoundError("project_not_found", name=project_name) from exc
            try:
                target = resolve_derivative_target(manager.load_project(project_name), char_name, derivative_name)
            except KeyError as exc:
                raise NotFoundError("character_not_found", name=char_name) from exc
            try:
                current_image = safe_join(project_path, target.sheet_path)
            except PathTraversalError as exc:
                raise HTTPException(
                    status_code=400, detail=_t("invalid_resource_id", resource_id=target.artifact_id)
                ) from exc
            return target, current_image, VersionManager(project_path)

        target, current_image, versions = await run_sync_transaction(_resolve)

        # 限定读入内存的字节数：Content-Length 缺失或被绕过时不至于 OOM
        content = await file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise UploadTooLargeError(max_bytes)
        try:
            # 规范路径固定为 .png，任何输入格式都归一成 PNG
            png_bytes = await asyncio.to_thread(normalize_storyboard_upload, content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_t("invalid_image_file")) from exc

        with project_change_source("webui"):
            # 暂存文件一落盘就登记：暂存进行中被取消时返回值随之丢弃，按登记回收；
            # 暂存成功后由 commit_manual_derivative_upload 的 finally 回收。
            staged_images: list[Path] = []

            def _stage() -> None:
                staged_images.append(stage_uploaded_bytes(png_bytes, current_image))

            try:
                await run_sync_transaction(_stage)
            except BaseException:
                for path in staged_images:
                    # hard-delete: temp -- 上传暂存文件
                    await run_noninterruptible_sync(path.unlink, missing_ok=True)
                raise
            try:
                version = await commit_manual_derivative_upload(
                    project_name=project_name,
                    target=target,
                    staged_image=staged_images[0],
                    current_image=current_image,
                    versions=versions,
                    original_filename=file.filename,
                )
            except KeyError as exc:
                # 解析之后、提交之前本体或衍生被并发删除
                raise NotFoundError("asset_derivative_not_found", name=derivative_name) from exc

            # 与衍生资产图生成完成同形状的变更事件；返回的指纹直接复用进响应体
            fingerprints = await run_sync_transaction(
                emit_generation_success_batch,
                task_type=DERIVATIVE_TASK_TYPE,
                project_name=project_name,
                resource_id=target.artifact_id,
                payload={},
            )

        return {
            "success": True,
            "path": target.sheet_path,
            "version": version,
            "asset_fingerprints": fingerprints,
        }

    except UploadValidationError as e:
        raise HTTPException(status_code=e.status_code, detail=_t(e.key, **e.params)) from e
    except (HTTPException, ApiError):
        raise
    except Exception as e:
        # 不回传 str(e)：未预期异常的消息可能含服务器路径等内部细节，堆栈进日志即可
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from e
