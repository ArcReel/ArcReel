"""
File management routes

Handles file uploads and static asset serving.
"""

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

from lib import PROJECT_ROOT
from lib.image_utils import normalize_uploaded_image
from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import ProjectManager
from server.auth import CurrentUser

router = APIRouter()

# Initialize project manager
pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


# Allowed file types
ALLOWED_EXTENSIONS = {
    "source": [".txt", ".md", ".doc", ".docx"],
    "character": [".png", ".jpg", ".jpeg", ".webp"],
    "character_ref": [".png", ".jpg", ".jpeg", ".webp"],
    "clue": [".png", ".jpg", ".jpeg", ".webp"],
    "storyboard": [".png", ".jpg", ".jpeg", ".webp"],
}


@router.get("/files/{project_name}/{path:path}")
async def serve_project_file(project_name: str, path: str, request: Request):
    """Serve static files (images/videos) within a project."""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            file_path = project_dir / path

            if not file_path.exists():
                raise HTTPException(status_code=404, detail=f"File not found: {path}")

            # Security check: ensure the path is within the project directory
            try:
                file_path.resolve().relative_to(project_dir.resolve())
            except ValueError:
                raise HTTPException(status_code=403, detail="Access to files outside the project directory is forbidden")

            return file_path

        file_path = await asyncio.to_thread(_sync)

        # Content-addressed cache: set immutable when ?v= param or versions/ path is present
        headers = {}
        if request.query_params.get("v") or path.startswith("versions/"):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"

        return FileResponse(file_path, headers=headers)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")


@router.post("/projects/{project_name}/upload/{upload_type}")
async def upload_file(
    project_name: str, upload_type: str, _user: CurrentUser, file: UploadFile = File(...), name: str = None
):
    """
    Upload a file

    Args:
        project_name: Project name
        upload_type: Upload type (source/character/clue/storyboard)
        file: The uploaded file
        name: Optional; used as character/clue name or storyboard ID (automatically updates metadata)
    """
    if upload_type not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Invalid upload type: {upload_type}")

    # Check file extension
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS[upload_type]:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {ext}; allowed types: {ALLOWED_EXTENSIONS[upload_type]}",
        )

    try:
        content = await file.read()

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)

            # Determine target directory
            if upload_type == "source":
                target_dir = project_dir / "source"
                filename = file.filename
            elif upload_type == "character":
                target_dir = project_dir / "characters"
                # Always save as PNG with a stable filename (avoids version restore/reference issues from jpg/png inconsistency)
                if name:
                    filename = f"{name}.png"
                else:
                    filename = f"{Path(file.filename).stem}.png"
            elif upload_type == "character_ref":
                target_dir = project_dir / "characters" / "refs"
                if name:
                    filename = f"{name}.png"
                else:
                    filename = f"{Path(file.filename).stem}.png"
            elif upload_type == "clue":
                target_dir = project_dir / "clues"
                if name:
                    filename = f"{name}.png"
                else:
                    filename = f"{Path(file.filename).stem}.png"
            elif upload_type == "storyboard":
                # Note: directory is storyboards (plural), not storyboard
                target_dir = project_dir / "storyboards"
                if name:
                    filename = f"scene_{name}.png"
                else:
                    filename = f"{Path(file.filename).stem}.png"
            else:
                target_dir = project_dir / upload_type
                filename = file.filename

            target_dir.mkdir(parents=True, exist_ok=True)

            # Save file (compress to JPEG if larger than 2MB, otherwise validate and save as-is)
            nonlocal content
            if upload_type in ("character", "character_ref", "clue", "storyboard"):
                try:
                    content, ext = normalize_uploaded_image(content, Path(file.filename).suffix.lower())
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid image file; could not be parsed")
                filename = Path(filename).with_suffix(ext).name

            target_path = target_dir / filename
            with open(target_path, "wb") as f:
                f.write(content)

            # Update metadata
            if upload_type == "source":
                relative_path = f"source/{filename}"
            elif upload_type == "character":
                relative_path = f"characters/{filename}"
            elif upload_type == "character_ref":
                relative_path = f"characters/refs/{filename}"
            elif upload_type == "clue":
                relative_path = f"clues/{filename}"
            elif upload_type == "storyboard":
                relative_path = f"storyboards/{filename}"
            else:
                relative_path = f"{upload_type}/{filename}"

            if upload_type == "character" and name:
                try:
                    with project_change_source("webui"):
                        get_project_manager().update_project_character_sheet(
                            project_name, name, f"characters/{filename}"
                        )
                except KeyError:
                    pass  # Character does not exist; ignore

            if upload_type == "character_ref" and name:
                try:
                    with project_change_source("webui"):
                        get_project_manager().update_character_reference_image(
                            project_name, name, f"characters/refs/{filename}"
                        )
                except KeyError:
                    pass  # Character does not exist; ignore

            if upload_type == "clue" and name:
                try:
                    with project_change_source("webui"):
                        get_project_manager().update_clue_sheet(
                            project_name,
                            name,
                            f"clues/{filename}",
                        )
                except KeyError:
                    pass  # Clue does not exist; ignore

            return {
                "success": True,
                "filename": filename,
                "path": relative_path,
                "url": f"/api/v1/files/{project_name}/{relative_path}",
            }

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_name}/files")
async def list_project_files(project_name: str, _user: CurrentUser):
    """List all files in a project"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)

            files = {
                "source": [],
                "characters": [],
                "clues": [],
                "storyboards": [],
                "videos": [],
                "output": [],
            }

            for subdir, file_list in files.items():
                subdir_path = project_dir / subdir
                if subdir_path.exists():
                    for f in subdir_path.iterdir():
                        if f.is_file() and not f.name.startswith("."):
                            file_list.append(
                                {
                                    "name": f.name,
                                    "size": f.stat().st_size,
                                    "url": f"/api/v1/files/{project_name}/{subdir}/{f.name}",
                                }
                            )

            return {"files": files}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_name}/source/{filename}")
async def get_source_file(project_name: str, filename: str, _user: CurrentUser):
    """Get the text content of a source file"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            source_path = project_dir / "source" / filename

            if not source_path.exists():
                raise HTTPException(status_code=404, detail=f"File not found: {filename}")

            # Security check: ensure path is within the project directory
            try:
                source_path.resolve().relative_to(project_dir.resolve())
            except ValueError:
                raise HTTPException(status_code=403, detail="Access to files outside the project directory is forbidden")

            return source_path.read_text(encoding="utf-8")

        content = await asyncio.to_thread(_sync)
        return PlainTextResponse(content)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File encoding error; could not read the file")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/projects/{project_name}/source/{filename}")
async def update_source_file(
    project_name: str, filename: str, _user: CurrentUser, content: str = Body(..., media_type="text/plain")
):
    """Update or create a source file"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            source_dir = project_dir / "source"
            source_dir.mkdir(parents=True, exist_ok=True)
            source_path = source_dir / filename

            # Security check: ensure path is within the project directory
            try:
                source_path.resolve().relative_to(project_dir.resolve())
            except ValueError:
                raise HTTPException(status_code=403, detail="Access to files outside the project directory is forbidden")

            source_path.write_text(content, encoding="utf-8")
            return {"success": True, "path": f"source/{filename}"}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_name}/source/{filename}")
async def delete_source_file(project_name: str, filename: str, _user: CurrentUser):
    """Delete a source file"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            source_path = project_dir / "source" / filename

            # Security check: ensure path is within the project directory
            try:
                source_path.resolve().relative_to(project_dir.resolve())
            except ValueError:
                raise HTTPException(status_code=403, detail="Access to files outside the project directory is forbidden")

            if source_path.exists():
                source_path.unlink()
                return {"success": True}
            else:
                raise HTTPException(status_code=404, detail=f"File not found: {filename}")

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Draft file management ====================


@router.get("/projects/{project_name}/drafts")
async def list_drafts(project_name: str, _user: CurrentUser):
    """List all draft directories and files for a project"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            drafts_dir = project_dir / "drafts"

            result = {}
            if drafts_dir.exists():
                for episode_dir in sorted(drafts_dir.iterdir()):
                    if episode_dir.is_dir() and episode_dir.name.startswith("episode_"):
                        episode_num = episode_dir.name.replace("episode_", "")
                        files = []
                        for f in sorted(episode_dir.glob("*.md")):
                            files.append(
                                {
                                    "name": f.name,
                                    "step": _extract_step_number(f.name),
                                    "title": _get_step_title(f.name),
                                    "size": f.stat().st_size,
                                    "modified": f.stat().st_mtime,
                                }
                            )
                        result[episode_num] = files

            return {"drafts": result}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")


def _extract_step_number(filename: str) -> int:
    """Extract the step number from a filename"""
    import re

    match = re.search(r"step(\d+)", filename)
    return int(match.group(1)) if match else 0


def _get_step_files(content_mode: str) -> dict:
    """Get the step filename mapping based on content_mode"""
    if content_mode == "narration":
        return {1: "step1_segments.md"}
    else:
        return {1: "step1_normalized_script.md"}


def _get_step_title(filename: str) -> str:
    """Get step title"""
    titles = {
        "step1_normalized_script.md": "Normalized Script",
        "step1_segments.md": "Segment Split",
    }
    return titles.get(filename, filename)


def _get_content_mode(project_dir: Path) -> str:
    """Read content_mode from project.json"""
    project_json_path = project_dir / "project.json"
    if project_json_path.exists():
        with open(project_json_path, encoding="utf-8") as f:
            project_data = json.load(f)
            return project_data.get("content_mode", "drama")
    return "drama"


@router.get("/projects/{project_name}/drafts/{episode}/step{step_num}")
async def get_draft_content(project_name: str, episode: int, step_num: int, _user: CurrentUser):
    """Get the draft content for a specific step"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            content_mode = _get_content_mode(project_dir)
            step_files = _get_step_files(content_mode)

            if step_num not in step_files:
                raise HTTPException(status_code=400, detail=f"Invalid step number: {step_num}")

            draft_path = project_dir / "drafts" / f"episode_{episode}" / step_files[step_num]

            if not draft_path.exists():
                raise HTTPException(status_code=404, detail="Draft file does not exist")

            return draft_path.read_text(encoding="utf-8")

        content = await asyncio.to_thread(_sync)
        return PlainTextResponse(content)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")


@router.put("/projects/{project_name}/drafts/{episode}/step{step_num}")
async def update_draft_content(
    project_name: str,
    episode: int,
    step_num: int,
    _user: CurrentUser,
    content: str = Body(..., media_type="text/plain"),
):
    """Update draft content"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            content_mode = _get_content_mode(project_dir)
            step_files = _get_step_files(content_mode)

            if step_num not in step_files:
                raise HTTPException(status_code=400, detail=f"Invalid step number: {step_num}")

            drafts_dir = project_dir / "drafts" / f"episode_{episode}"
            drafts_dir.mkdir(parents=True, exist_ok=True)

            draft_path = drafts_dir / step_files[step_num]
            is_new = not draft_path.exists()
            draft_path.write_text(content, encoding="utf-8")

            # Emit draft event to notify the frontend
            action = "created" if is_new else "updated"
            label_prefix = "Segment Split" if content_mode == "narration" else "Normalized Script"
            change = {
                "entity_type": "draft",
                "action": action,
                "entity_id": f"episode_{episode}_step{step_num}",
                "label": f"Episode {episode} {label_prefix}",
                "episode": episode,
                "focus": {
                    "pane": "episode",
                    "episode": episode,
                },
                "important": is_new,
            }
            try:
                emit_project_change_batch(project_name, [change], source="worker")
            except Exception:
                logger.warning("Failed to send draft event project=%s episode=%s", project_name, episode, exc_info=True)

            return {"success": True, "path": str(draft_path.relative_to(project_dir))}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")


@router.delete("/projects/{project_name}/drafts/{episode}/step{step_num}")
async def delete_draft(project_name: str, episode: int, step_num: int, _user: CurrentUser):
    """Delete a draft file"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            content_mode = _get_content_mode(project_dir)
            step_files = _get_step_files(content_mode)

            if step_num not in step_files:
                raise HTTPException(status_code=400, detail=f"Invalid step number: {step_num}")

            draft_path = project_dir / "drafts" / f"episode_{episode}" / step_files[step_num]

            if draft_path.exists():
                draft_path.unlink()
                return {"success": True}
            else:
                raise HTTPException(status_code=404, detail="Draft file does not exist")

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")


# ==================== Style reference image management ====================


@router.post("/projects/{project_name}/style-image")
async def upload_style_image(project_name: str, _user: CurrentUser, file: UploadFile = File(...)):
    """
    Upload a style reference image and analyze the style

    1. Save the image to projects/{project_name}/style_reference.png
    2. Call the Gemini API to analyze the style
    3. Update the style_image and style_description fields in project.json
    """
    # Check file type
    ext = Path(file.filename).suffix.lower()
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {ext}; allowed types: .png, .jpg, .jpeg, .webp",
        )

    try:
        content = await file.read()

        def _sync_prepare():
            project_dir = get_project_manager().get_project_path(project_name)
            try:
                content_norm, new_ext = normalize_uploaded_image(content, Path(file.filename).suffix.lower())
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid image file; could not be parsed")
            style_filename = f"style_reference{new_ext}"

            output_path = project_dir / style_filename
            with open(output_path, "wb") as f:
                f.write(content_norm)

            return output_path, style_filename

        output_path, style_filename = await asyncio.to_thread(_sync_prepare)

        # Call TextGenerator to analyze the style (automatically tracks usage)
        from lib.text_backends.base import ImageInput, TextGenerationRequest, TextTaskType
        from lib.text_backends.prompts import STYLE_ANALYSIS_PROMPT
        from lib.text_generator import TextGenerator

        generator = await TextGenerator.create(TextTaskType.STYLE_ANALYSIS, project_name)
        result = await generator.generate(
            TextGenerationRequest(prompt=STYLE_ANALYSIS_PROMPT, images=[ImageInput(path=output_path)]),
            project_name=project_name,
        )
        style_description = result.text

        def _sync_save():
            # Update project.json
            project_data = get_project_manager().load_project(project_name)
            project_data["style_image"] = style_filename
            project_data["style_description"] = style_description
            with project_change_source("webui"):
                get_project_manager().save_project(project_name, project_data)

        await asyncio.to_thread(_sync_save)

        return {
            "success": True,
            "style_image": style_filename,
            "style_description": style_description,
            "url": f"/api/v1/files/{project_name}/{style_filename}",
        }

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_name}/style-image")
async def delete_style_image(project_name: str, _user: CurrentUser):
    """
    Delete the style reference image and its related fields
    """
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)

            # Delete the image file (compatible with all possible extensions)
            for suffix in (".jpg", ".jpeg", ".png", ".webp"):
                image_path = project_dir / f"style_reference{suffix}"
                if image_path.exists():
                    image_path.unlink()

            # Clear related fields in project.json
            project_data = get_project_manager().load_project(project_name)
            project_data.pop("style_image", None)
            project_data.pop("style_description", None)
            with project_change_source("webui"):
                get_project_manager().save_project(project_name, project_data)

            return {"success": True}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_name}/style-description")
async def update_style_description(
    project_name: str, _user: CurrentUser, style_description: str = Body(..., embed=True)
):
    """
    Update style description (manual edit)
    """
    try:

        def _sync():
            project_data = get_project_manager().load_project(project_name)
            project_data["style_description"] = style_description
            with project_change_source("webui"):
                get_project_manager().save_project(project_name, project_data)

            return {"success": True, "style_description": style_description}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))
