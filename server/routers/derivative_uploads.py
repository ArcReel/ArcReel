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

from fastapi import APIRouter, File, HTTPException, UploadFile

from lib.infra.api_errors import ApiError
from lib.infra.async_thread import run_sync_transaction
from lib.infra.image_utils import normalize_storyboard_upload
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from server.i18n import Translator
from server.services.currency.upload_finalize import (
    UploadTooLargeError,
    UploadValidationError,
    install_manual_derivative_upload,
    resolve_derivative_upload_target,
    validate_upload,
)

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
        upload = await run_sync_transaction(
            resolve_derivative_upload_target, get_project_manager(), project_name, char_name, derivative_name
        )

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
            installed = await install_manual_derivative_upload(
                project_name=project_name,
                upload=upload,
                png_bytes=png_bytes,
                original_filename=file.filename,
            )

        return {
            "success": True,
            "path": upload.target.sheet_path,
            "version": installed.version,
            "asset_fingerprints": installed.asset_fingerprints,
        }

    except UploadValidationError as e:
        raise HTTPException(status_code=e.status_code, detail=_t(e.key, **e.params)) from e
    except (HTTPException, ApiError):
        raise
    except Exception as e:
        # 不回传 str(e)：未预期异常的消息可能含服务器路径等内部细节，堆栈进日志即可
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from e
