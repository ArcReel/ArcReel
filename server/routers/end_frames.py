"""Routes for setting and clearing storyboard end frames.

Two input paths share one destination: ``/end-frame/upload`` accepts multipart uploads; ``/end-frame/select`` selects
an existing image by project-relative path. Both normalize a PNG snapshot to ``end_frames/scene_{id}.png``.
``DELETE /end-frame`` removes the snapshot and clears the field.

Only these routes write ``end_frame_image``. The generic script PATCH allowlist deliberately excludes it so raw values
cannot bypass snapshot copying and reintroduce dangling references or out-of-bounds paths."""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from lib.infra.api_errors import ApiError, NotFoundError
from lib.script.script_editor import ScriptEditError
from server.error_handlers import script_edit_detail
from server.i18n import Translator
from server.services.currency.upload_finalize import (
    UploadTooLargeError,
    UploadValidationError,
    validate_upload,
)
from server.services.project.end_frame import (
    EndFrameError,
    clear_end_frame,
    set_end_frame_from_bytes,
    set_end_frame_from_project_image,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class SelectEndFrameRequest(BaseModel):
    script_file: str
    source_path: str


@contextmanager
def _translated_errors(script_file: str, _t: Translator) -> Generator[None]:
    """Domain-error-to-HTTP mapping shared by all three endpoints."""
    try:
        yield
    except (EndFrameError, UploadValidationError) as e:
        raise HTTPException(status_code=e.status_code, detail=_t(e.key, **e.params)) from e
    except FileNotFoundError as exc:
        # Never return str(exc): load_script errors contain absolute server paths.
        raise NotFoundError("script_not_found", name=script_file) from exc
    except ScriptEditError as e:
        raise HTTPException(status_code=400, detail=script_edit_detail(e, _t)) from e
    except (HTTPException, ApiError):
        raise
    except Exception as e:
        # Never return str(e): unexpected errors may contain server paths or other internals; log the traceback only.
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from e


@router.post("/projects/{project_name}/shots/{shot_id}/end-frame/upload")
async def upload_end_frame(
    project_name: str,
    shot_id: str,
    script_file: str,
    _t: Translator,
    file: UploadFile = File(...),
):
    """Upload any image as this storyboard’s end frame."""
    with _translated_errors(script_file, _t):
        max_bytes = validate_upload(file.filename, file.size, kind="image")
        # Bound bytes read into memory to prevent OOM when Content-Length is absent/bypassed.
        content = await file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise UploadTooLargeError(max_bytes)

        relative = await set_end_frame_from_bytes(
            project_name=project_name,
            script_file=script_file,
            shot_id=shot_id,
            content=content,
        )
        return {"success": True, "end_frame_image": relative}


@router.post("/projects/{project_name}/shots/{shot_id}/end-frame/select")
async def select_end_frame(
    project_name: str,
    shot_id: str,
    req: SelectEndFrameRequest,
    _t: Translator,
):
    """Select an existing project image by relative path as this storyboard’s end frame, copying a snapshot rather than creating a reference."""
    with _translated_errors(req.script_file, _t):
        relative = await set_end_frame_from_project_image(
            project_name=project_name,
            script_file=req.script_file,
            shot_id=shot_id,
            source_path=req.source_path,
        )
        return {"success": True, "end_frame_image": relative}


@router.delete("/projects/{project_name}/shots/{shot_id}/end-frame")
async def delete_end_frame(
    project_name: str,
    shot_id: str,
    script_file: str,
    _t: Translator,
):
    """Clear this storyboard’s end frame: delete the snapshot and clear the field."""
    with _translated_errors(script_file, _t):
        await clear_end_frame(project_name=project_name, script_file=script_file, shot_id=shot_id)
        return {"success": True}
