"""Shot-level manual storyboard-image/video upload routes.

Separate from generic asset uploads (files.py), outside UPLOAD_SPECS: locate entries by script_file + shot_id,
track versions with VersionManager, write metadata through finalize_shot_*, and return asset_fingerprints for immediate
uploader cache busting, with SSE covering other clients. UploadSpec subdir + naming paths and MetadataSetter signatures
do not cover this pipeline. Share extension/size validation through upload_finalize.validate_upload.

Split-grid cells use normal storyboard-to-video canonical paths, storyboards/scene_{id}.png. Uploading a shot overwrites
its cell without changing grid records; whole-composite uploads use separate grids.py endpoints."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile

from lib.artifacts.version_manager import VersionManager
from lib.infra.api_errors import ApiError, NotFoundError
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.infra.image_utils import normalize_storyboard_upload
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from lib.project.resource_paths import resource_relative_path
from lib.script.script_editor import ScriptEditError
from lib.script.storyboard_sequence import find_storyboard_item, get_storyboard_items
from server.error_handlers import script_edit_detail
from server.i18n import Translator
from server.services.currency.upload_finalize import (
    UploadTooLargeError,
    UploadValidationError,
    commit_manual_storyboard_upload,
    finalize_shot_video_upload,
    stage_uploaded_bytes,
    stage_uploaded_video_stream,
    validate_upload,
)
from server.services.tasks.generation_tasks import emit_generation_success_batch

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
    """Upload a storyboard image or video, replacing the shot’s AI-generated asset.

    Match generation: staging → atomic formal selection/metadata commit with derived status → SSE batch notification."""
    try:
        max_bytes = validate_upload(file.filename, file.size, kind="image" if kind == "storyboard" else "video")

        resource_type = "storyboards" if kind == "storyboard" else "videos"
        relative_path = resource_relative_path(resource_type, shot_id)

        def _validate_shot() -> tuple[Path, VersionManager]:
            # Map missing projects separately; outer FileNotFoundError handling would incorrectly report a missing script.
            try:
                project_path = get_project_manager().get_project_path(project_name)
            except FileNotFoundError as exc:
                raise NotFoundError("project_not_found", name=project_name) from exc
            script = get_project_manager().load_script(project_name, script_file)
            # reference_video scripts return an empty list → 404; uploads for that mode use reference-videos routes.
            items, id_field, _, _, _ = get_storyboard_items(script)
            if find_storyboard_item(items, id_field, shot_id) is None:
                raise HTTPException(status_code=404, detail=_t("segment_not_found", id=shot_id))
            # Path traversal guard: absolute paths built from shot_id must stay within the project, matching versions.py.
            try:
                safe_join(project_path, relative_path)
            except PathTraversalError as exc:
                raise HTTPException(status_code=400, detail=_t("invalid_resource_id", resource_id=shot_id)) from exc
            return project_path, VersionManager(project_path)

        project_path, versions = await run_sync_transaction(_validate_shot)
        target = project_path / relative_path

        with project_change_source("webui"):
            if kind == "storyboard":
                # Bound bytes read into memory to prevent OOM when Content-Length is absent/bypassed.
                content = await file.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise UploadTooLargeError(max_bytes)
                try:
                    png_bytes = await asyncio.to_thread(normalize_storyboard_upload, content)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=_t("invalid_image_file")) from exc
                # Register staged files immediately on persistence; cancellation loses the return value, so reclaim through registration;
                # after successful staging, commit_manual_storyboard_upload finally handles cleanup.
                staged_images: list[Path] = []

                def _stage() -> None:
                    staged_images.append(stage_uploaded_bytes(png_bytes, target))

                try:
                    await run_sync_transaction(_stage)
                except BaseException:
                    for path in staged_images:
                        # hard-delete: temp -- staged upload file
                        await run_noninterruptible_sync(path.unlink, missing_ok=True)
                    raise
                version = await commit_manual_storyboard_upload(
                    project_name=project_name,
                    script_file=script_file,
                    shot_id=shot_id,
                    asset_path=relative_path,
                    staged_image=staged_images[0],
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

            # emit reads scripts to resolve episodes and compute fingerprints; use a thread pool to avoid blocking the event loop;
            # reuse returned fingerprints in responses without recomputing.
            fingerprints = await run_sync_transaction(
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
        # Never return str(exc): load_script errors contain absolute server paths.
        raise NotFoundError("script_not_found", name=script_file) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_t("segment_not_found", id=shot_id)) from exc
    except ScriptEditError as e:
        raise HTTPException(status_code=400, detail=script_edit_detail(e, _t)) from e
    except (HTTPException, ApiError):
        raise
    except Exception as e:
        # Never return str(e): unexpected errors may contain server paths or other internals; log the traceback only.
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from e
