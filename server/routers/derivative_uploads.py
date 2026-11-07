"""Routes for replacing character-derivative asset images by upload (``docs/adr/0072``).

Uploads are finished images: save to canonical ``characters/derivatives/{角色}/{衍生}.png`` as a selected manually uploaded
version in history. The manifest records image bytes (``docs/adr/0062``), independent of the base image or change description.
Uploads replace only the asset image, not the base for subsequent edit generation; the change description remains unchanged.

Separate from base-image uploads (``files.py`` ``UPLOAD_SPECS``): derivative coordinates have two components, base/derivative,
and persist with fixed ``.png`` extensions. Submission uses the formal activation callback shared with derivative generation and image editing."""

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
    """Upload an image to replace a character derivative’s asset image as its current version.

    No base asset image is required: the upload is a finished image, not generated from the base image."""
    try:
        max_bytes = validate_upload(file.filename, file.size, kind="image")
        upload = await run_sync_transaction(
            resolve_derivative_upload_target, get_project_manager(), project_name, char_name, derivative_name
        )

        # Bound bytes read into memory to prevent OOM when Content-Length is absent or bypassed.
        content = await file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise UploadTooLargeError(max_bytes)
        try:
            # Canonical paths use .png; normalize all input formats to PNG.
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
        # Never return str(e): unexpected errors may contain server paths or other internals; log the traceback only.
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from e
