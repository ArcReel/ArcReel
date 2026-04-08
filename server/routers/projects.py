"""
Project management routes

Handles CRUD operations for projects, leveraging lib/project_manager.py.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from server.services.jianying_draft_service import JianyingDraftService

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi import Path as FastAPIPath
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

logger = logging.getLogger(__name__)

from lib import PROJECT_ROOT
from lib.asset_fingerprints import compute_asset_fingerprints
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from lib.status_calculator import StatusCalculator
from server.auth import CurrentUser, create_download_token, verify_download_token
from server.routers._validators import validate_backend_value
from server.services.project_archive import (
    ProjectArchiveService,
    ProjectArchiveValidationError,
)

router = APIRouter()

# Initialize project manager and status calculator
pm = ProjectManager(PROJECT_ROOT / "projects")
calc = StatusCalculator(pm)


def get_project_manager() -> ProjectManager:
    return pm


def get_status_calculator() -> StatusCalculator:
    return calc


def get_archive_service() -> ProjectArchiveService:
    return ProjectArchiveService(get_project_manager())


class CreateProjectRequest(BaseModel):
    name: str | None = None
    title: str | None = None
    style: str | None = ""
    content_mode: str | None = "narration"
    aspect_ratio: str | None = "9:16"
    default_duration: int | None = None


class UpdateProjectRequest(BaseModel):
    title: str | None = None
    style: str | None = None
    content_mode: str | None = None
    aspect_ratio: str | None = None
    default_duration: int | None = None
    video_backend: str | None = None
    image_backend: str | None = None
    video_generate_audio: bool | None = None
    text_backend_script: str | None = None
    text_backend_overview: str | None = None
    text_backend_style: str | None = None


def _cleanup_temp_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


def _cleanup_temp_dir(dir_path: str) -> None:
    shutil.rmtree(dir_path, ignore_errors=True)


@router.post("/projects/import")
async def import_project_archive(
    _user: CurrentUser,
    file: UploadFile = File(...),
    conflict_policy: str = Form("prompt"),
):
    """Import a project from a ZIP archive."""
    upload_path: str | None = None
    try:
        fd, upload_path = tempfile.mkstemp(prefix="arcreel-upload-", suffix=".zip")
        os.close(fd)

        with open(upload_path, "wb") as target:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)

        def _sync():
            return get_archive_service().import_project_archive(
                Path(upload_path),
                uploaded_filename=file.filename,
                conflict_policy=conflict_policy,
            )

        result = await asyncio.to_thread(_sync)
        return {
            "success": True,
            "project_name": result.project_name,
            "project": result.project,
            "warnings": result.warnings,
            "conflict_resolution": result.conflict_resolution,
            "diagnostics": result.diagnostics,
        }
    except ProjectArchiveValidationError as exc:
        diagnostics = exc.extra.get(
            "diagnostics",
            {"blocking": [], "auto_fixable": [], "warnings": []},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "errors": exc.errors,
                "warnings": exc.warnings,
                "diagnostics": diagnostics,
                **exc.extra,
            },
        )
    except Exception as e:
        logger.exception("Request processing failed")
        return JSONResponse(
            status_code=500,
            content={"detail": str(e), "errors": [], "warnings": []},
        )
    finally:
        await file.close()
        if upload_path:
            _cleanup_temp_file(upload_path)


@router.post("/projects/{name}/export/token")
async def create_export_token(
    name: str,
    current_user: CurrentUser,
    scope: str = Query("full"),
):
    """Issue a short-lived download token for browser-native download authentication."""
    try:
        if scope not in ("full", "current"):
            raise HTTPException(status_code=422, detail="scope must be 'full' or 'current'")

        def _sync():
            if not get_project_manager().project_exists(name):
                raise HTTPException(status_code=404, detail=f"Project '{name}' does not exist or is not initialized")
            return get_archive_service().get_export_diagnostics(name, scope=scope)

        diagnostics = await asyncio.to_thread(_sync)
        username = current_user.sub
        download_token = create_download_token(username, name)
        return {
            "download_token": download_token,
            "expires_in": 300,
            "diagnostics": diagnostics,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{name}/export")
async def export_project_archive(
    name: str,
    download_token: str = Query(...),
    scope: str = Query("full"),
):
    """Export a project as a ZIP. Requires download_token authentication (obtained via POST /export/token)."""
    if scope not in ("full", "current"):
        raise HTTPException(status_code=422, detail="scope must be 'full' or 'current'")

    # Verify download_token
    import jwt as pyjwt

    try:
        verify_download_token(download_token, name)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Download link has expired; please re-export")
    except ValueError:
        raise HTTPException(status_code=403, detail="Download token does not match the target project")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Download token is invalid")

    try:
        archive_path, download_name = await asyncio.to_thread(
            lambda: get_archive_service().export_project(name, scope=scope)
        )
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=download_name,
            background=BackgroundTask(_cleanup_temp_file, str(archive_path)),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{name}' does not exist or is not initialized")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


# --- JianyingDraft export ---


def get_jianying_draft_service() -> JianyingDraftService:
    from server.services.jianying_draft_service import JianyingDraftService

    return JianyingDraftService(get_project_manager())


def _validate_draft_path(draft_path: str) -> str:
    """Validate the draft_path value."""
    if not draft_path or not draft_path.strip():
        raise HTTPException(status_code=422, detail="Please provide a valid JianyingDraft directory path")
    if len(draft_path) > 1024:
        raise HTTPException(status_code=422, detail="Draft directory path is too long")
    if any(ord(c) < 32 for c in draft_path):
        raise HTTPException(status_code=422, detail="Draft directory path contains invalid characters")
    return draft_path.strip()


@router.get("/projects/{name}/export/jianying-draft")
def export_jianying_draft(
    name: str,
    episode: int = Query(..., description="Episode number"),
    draft_path: str = Query(..., description="User's local JianyingDraft directory"),
    download_token: str = Query(..., description="Download token"),
    jianying_version: str = Query("6", description="Jianying version: 6 or 5"),
):
    """Export a JianyingDraft ZIP for the specified episode."""
    import jwt as pyjwt

    # 1. Verify download_token
    try:
        verify_download_token(download_token, name)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Download link has expired; please re-export")
    except ValueError:
        raise HTTPException(status_code=403, detail="Download token does not match the project")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Download token is invalid")

    # 2. Validate draft_path
    draft_path = _validate_draft_path(draft_path)

    # 3. Call service
    svc = get_jianying_draft_service()
    try:
        zip_path = svc.export_episode_draft(
            project_name=name,
            episode=episode,
            draft_path=draft_path,
            use_draft_info_name=(jianying_version != "5"),
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        logger.exception("JianyingDraft export failed: project=%s episode=%d", name, episode)
        raise HTTPException(status_code=500, detail="JianyingDraft export failed; please try again later")

    download_name = f"{name}_episode{episode}_jianying_draft.zip"

    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=download_name,
        background=BackgroundTask(_cleanup_temp_dir, str(zip_path.parent)),
    )


@router.get("/projects")
async def list_projects(_user: CurrentUser):
    """List all projects."""

    def _sync():
        manager = get_project_manager()
        calculator = get_status_calculator()
        projects = []
        for name in manager.list_projects():
            try:
                # Try to load project metadata
                if manager.project_exists(name):
                    project = manager.load_project(name)
                    # Get thumbnail (first storyboard image)
                    project_dir = manager.get_project_path(name)
                    storyboards_dir = project_dir / "storyboards"
                    thumbnail = None
                    if storyboards_dir.exists():
                        scene_images = sorted(storyboards_dir.glob("scene_*.png"))
                        if scene_images:
                            thumbnail = f"/api/v1/files/{name}/storyboards/{scene_images[0].name}"

                    # Use StatusCalculator to compute progress (calculated at read time)
                    status = calculator.calculate_project_status(name, project)

                    projects.append(
                        {
                            "name": name,
                            "title": project.get("title", name),
                            "style": project.get("style", ""),
                            "thumbnail": thumbnail,
                            "status": status,
                        }
                    )
                else:
                    # Project without project.json
                    projects.append(
                        {
                            "name": name,
                            "title": name,
                            "style": "",
                            "thumbnail": None,
                            "status": {},
                        }
                    )
            except Exception as e:
                # Return basic info on error
                logger.warning("Failed to load metadata for project '%s': %s", name, e)
                projects.append(
                    {"name": name, "title": name, "style": "", "thumbnail": None, "status": {}, "error": str(e)}
                )

        return {"projects": projects}

    return await asyncio.to_thread(_sync)


@router.post("/projects")
async def create_project(req: CreateProjectRequest, _user: CurrentUser):
    """Create a new project."""
    try:

        def _sync():
            manager = get_project_manager()
            title = (req.title or "").strip()
            manual_name = (req.name or "").strip()
            if not title and not manual_name:
                raise HTTPException(status_code=400, detail="Project title cannot be empty")
            project_name = manual_name or manager.generate_project_name(title)

            try:
                manager.create_project(project_name)
            except FileExistsError:
                raise HTTPException(status_code=400, detail=f"Project '{project_name}' already exists")
            with project_change_source("webui"):
                project = manager.create_project_metadata(
                    project_name,
                    title or manual_name,
                    req.style,
                    req.content_mode,
                    aspect_ratio=req.aspect_ratio,
                    default_duration=req.default_duration,
                )
            return {"success": True, "name": project_name, "project": project}

        return await asyncio.to_thread(_sync)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{name}")
async def get_project(name: str, _user: CurrentUser):
    """Get project details (including calculated fields)."""
    try:

        def _sync():
            manager = get_project_manager()
            calculator = get_status_calculator()
            if not manager.project_exists(name):
                raise HTTPException(status_code=404, detail=f"Project '{name}' does not exist or is not initialized")

            project = manager.load_project(name)

            # Inject calculated fields (not written to JSON; for API response only)
            project = calculator.enrich_project(name, project)

            # Load all scripts and inject calculated fields
            scripts = {}
            for ep in project.get("episodes", []):
                script_file = ep.get("script_file", "")
                if script_file:
                    try:
                        script = manager.load_script(name, script_file)
                        script = calculator.enrich_script(script)
                        key = (
                            script_file.replace("scripts/", "", 1)
                            if script_file.startswith("scripts/")
                            else script_file
                        )
                        scripts[key] = script
                    except FileNotFoundError:
                        logger.debug("Script file not found, skipping: %s/%s", name, script_file)

            # Compute media file fingerprints (for frontend content-addressed caching)
            project_path = manager.get_project_path(name)
            fingerprints = compute_asset_fingerprints(project_path)

            return {
                "project": project,
                "scripts": scripts,
                "asset_fingerprints": fingerprints,
            }

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{name}' does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{name}")
async def update_project(name: str, req: UpdateProjectRequest, _user: CurrentUser):
    """Update project metadata."""
    try:

        def _sync():
            manager = get_project_manager()
            project = manager.load_project(name)

            if req.content_mode is not None:
                raise HTTPException(
                    status_code=400,
                    detail="content_mode cannot be changed after project creation",
                )

            if req.title is not None:
                project["title"] = req.title
            if req.style is not None:
                project["style"] = req.style
            for field in (
                "video_backend",
                "image_backend",
                "text_backend_script",
                "text_backend_overview",
                "text_backend_style",
            ):
                if field in req.model_fields_set:
                    value = getattr(req, field)
                    if value:
                        validate_backend_value(value, field)
                        project[field] = value
                    else:
                        project.pop(field, None)
            if "video_generate_audio" in req.model_fields_set:
                if req.video_generate_audio is None:
                    project.pop("video_generate_audio", None)
                else:
                    project["video_generate_audio"] = req.video_generate_audio
            if "aspect_ratio" in req.model_fields_set and req.aspect_ratio is not None:
                project["aspect_ratio"] = req.aspect_ratio
            if "default_duration" in req.model_fields_set:
                if req.default_duration is None:
                    project.pop("default_duration", None)
                else:
                    project["default_duration"] = req.default_duration

            with project_change_source("webui"):
                manager.save_project(name, project)
            return {"success": True, "project": project}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{name}' does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{name}")
async def delete_project(name: str, _user: CurrentUser):
    """Delete a project."""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(name)
            shutil.rmtree(project_dir)
            return {"success": True, "message": f"Project '{name}' has been deleted"}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{name}' does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{name}/scripts/{script_file}")
async def get_script(name: str, script_file: str, _user: CurrentUser):
    """Get script content."""
    try:
        script = await asyncio.to_thread(get_project_manager().load_script, name, script_file)
        return {"script": script}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Script '{script_file}' does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


class UpdateSceneRequest(BaseModel):
    script_file: str
    updates: dict


@router.patch("/projects/{name}/scenes/{scene_id}")
async def update_scene(name: str, scene_id: str, req: UpdateSceneRequest, _user: CurrentUser):
    """Update a scene."""
    try:

        def _sync():
            manager = get_project_manager()
            script = manager.load_script(name, req.script_file)

            # Find and update the scene
            scene_found = False
            for scene in script.get("scenes", []):
                if scene.get("scene_id") == scene_id:
                    scene_found = True
                    # Update allowed fields
                    for key, value in req.updates.items():
                        if key in [
                            "duration_seconds",
                            "image_prompt",
                            "video_prompt",
                            "characters_in_scene",
                            "clues_in_scene",
                            "segment_break",
                            "note",
                        ]:
                            if value is None and key != "note":
                                continue
                            scene[key] = value
                    break

            if not scene_found:
                raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' does not exist")

            with project_change_source("webui"):
                manager.save_script(name, script, req.script_file)
            return {"success": True, "scene": scene}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Script does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


class UpdateSegmentRequest(BaseModel):
    script_file: str
    duration_seconds: int | None = None
    segment_break: bool | None = None
    image_prompt: dict | str | None = None
    video_prompt: dict | str | None = None
    transition_to_next: str | None = None
    note: str | None = None


class UpdateOverviewRequest(BaseModel):
    synopsis: str | None = None
    genre: str | None = None
    theme: str | None = None
    world_setting: str | None = None


@router.patch("/projects/{name}/segments/{segment_id}")
async def update_segment(name: str, segment_id: str, req: UpdateSegmentRequest, _user: CurrentUser):
    """Update a narration-mode segment."""
    try:

        def _sync():
            manager = get_project_manager()
            script = manager.load_script(name, req.script_file)

            # Check whether this is narration mode
            if script.get("content_mode") != "narration" and "segments" not in script:
                raise HTTPException(status_code=400, detail="This script is not in narration mode; please use the scene update endpoint")

            # Find and update the segment
            segment_found = False
            for segment in script.get("segments", []):
                if segment.get("segment_id") == segment_id:
                    segment_found = True
                    if req.duration_seconds is not None:
                        segment["duration_seconds"] = req.duration_seconds
                    if req.segment_break is not None:
                        segment["segment_break"] = req.segment_break
                    if req.image_prompt is not None:
                        segment["image_prompt"] = req.image_prompt
                    if req.video_prompt is not None:
                        segment["video_prompt"] = req.video_prompt
                    if req.transition_to_next is not None:
                        segment["transition_to_next"] = req.transition_to_next
                    if "note" in req.model_fields_set:
                        segment["note"] = req.note
                    break

            if not segment_found:
                raise HTTPException(status_code=404, detail=f"Segment '{segment_id}' does not exist")

            with project_change_source("webui"):
                manager.save_script(name, script, req.script_file)
            return {"success": True, "segment": segment}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Script does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Source file management ====================


@router.post("/projects/{name}/source")
async def set_project_source(
    name: Annotated[str, FastAPIPath(pattern=r"^[a-zA-Z0-9_-]+$")],
    _user: CurrentUser,
    generate_overview: Annotated[bool, Form()] = True,
    content: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
):
    """Upload a novel source file or submit text content directly, optionally triggering AI overview generation.

    Two mutually exclusive input methods (both use multipart/form-data):
    - file: upload a .txt/.md file; filename is taken from the uploaded file
    - content: submit text content directly; auto-named as novel.txt

    Maximum 200,000 characters.
    """
    MAX_CHARS = 200_000
    ALLOWED_SUFFIXES = {".txt", ".md"}

    if not content and not file:
        raise HTTPException(status_code=400, detail="Please provide either content (text) or file (file upload)")
    if content and file:
        raise HTTPException(status_code=400, detail="content and file cannot both be provided; please choose one")

    try:
        manager = get_project_manager()

        # Asynchronously read the uploaded file
        raw: bytes | None = None
        if file:
            original_name = file.filename or "novel.txt"
            suffix = Path(original_name).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise HTTPException(status_code=400, detail=f"Only .txt / .md files are supported; received: {original_name!r}")
            if file.size is not None and file.size > MAX_CHARS * 4:
                raise HTTPException(status_code=400, detail=f"File size exceeds the limit (maximum ~{MAX_CHARS} characters)")
            raw = await file.read()

        # Synchronous file I/O executed in a thread
        def _sync_write():
            if not manager.project_exists(name):
                raise HTTPException(status_code=404, detail=f"Project '{name}' does not exist")
            project_dir = manager.get_project_path(name)
            source_dir = project_dir / "source"
            source_dir.mkdir(parents=True, exist_ok=True)

            if raw is not None:
                safe_filename = Path(original_name).name
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    raise HTTPException(status_code=400, detail="File encoding error; please use a UTF-8 encoded text file")
                if len(text) > MAX_CHARS:
                    raise HTTPException(
                        status_code=400, detail=f"File content exceeds the maximum limit of {MAX_CHARS} characters (current: {len(text)})"
                    )
                (source_dir / safe_filename).write_text(text, encoding="utf-8")
                return safe_filename, len(text)
            else:
                if len(content) > MAX_CHARS:
                    raise HTTPException(
                        status_code=400, detail=f"content exceeds the maximum length of {MAX_CHARS} characters (current: {len(content)})"
                    )
                safe_filename = "novel.txt"
                (source_dir / safe_filename).write_text(content, encoding="utf-8")
                return safe_filename, len(content)

        safe_filename, chars = await asyncio.to_thread(_sync_write)

        result: dict = {"success": True, "filename": safe_filename, "chars": chars}

        if generate_overview:
            try:
                with project_change_source("webui"):
                    overview = await manager.generate_overview(name)
                result["overview"] = overview
            except Exception as ov_err:
                result["overview"] = None
                result["overview_error"] = str(ov_err)

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if file:
            await file.close()


# ==================== Project overview management ====================


@router.post("/projects/{name}/generate-overview")
async def generate_overview(name: str, _user: CurrentUser):
    """Generate project overview using AI."""
    try:
        with project_change_source("webui"):
            overview = await get_project_manager().generate_overview(name)
        return {"success": True, "overview": overview}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{name}' does not exist")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{name}/overview")
async def update_overview(name: str, req: UpdateOverviewRequest, _user: CurrentUser):
    """Update project overview (manual edit)."""
    try:

        def _sync():
            manager = get_project_manager()
            project = manager.load_project(name)

            if "overview" not in project:
                project["overview"] = {}

            if req.synopsis is not None:
                project["overview"]["synopsis"] = req.synopsis
            if req.genre is not None:
                project["overview"]["genre"] = req.genre
            if req.theme is not None:
                project["overview"]["theme"] = req.theme
            if req.world_setting is not None:
                project["overview"]["world_setting"] = req.world_setting

            with project_change_source("webui"):
                manager.save_project(name, project)
            return {"success": True, "overview": project["overview"]}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{name}' does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))
