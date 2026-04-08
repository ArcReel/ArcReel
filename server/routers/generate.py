"""
Generation API routes

Handles generation requests for storyboard images, videos, character images, and clue images.
All generation requests are enqueued to the GenerationQueue and executed asynchronously by GenerationWorker.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib import PROJECT_ROOT
from lib.generation_queue import get_generation_queue
from lib.project_manager import ProjectManager
from lib.prompt_utils import (
    is_structured_image_prompt,
    is_structured_video_prompt,
)
from lib.storyboard_sequence import (
    find_storyboard_item,
    get_storyboard_items,
)
from server.auth import CurrentUser

router = APIRouter()

# Initialize manager
pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


# ==================== Request models ====================


class GenerateStoryboardRequest(BaseModel):
    prompt: str | dict
    script_file: str


class GenerateVideoRequest(BaseModel):
    prompt: str | dict
    script_file: str
    duration_seconds: int | None = None  # Changed to None; resolved by the service layer
    seed: int | None = None


class GenerateCharacterRequest(BaseModel):
    prompt: str


class GenerateClueRequest(BaseModel):
    prompt: str


_LEGACY_PROVIDER_NAMES: dict[str, str] = {
    "gemini": "gemini-aistudio",
    "aistudio": "gemini-aistudio",
    "vertex": "gemini-vertex",
}


def _normalize_provider_id(raw: str) -> str:
    """Normalize a legacy-format provider name to the standard provider_id."""
    return _LEGACY_PROVIDER_NAMES.get(raw, raw)


def _snapshot_image_backend(project_name: str) -> dict:
    """Snapshot the image provider configuration and return a dict that can be merged into a payload.

    Priority: project-level image_backend > system-level default_image_backend.
    """
    project = get_project_manager().load_project(project_name)
    project_image_backend = project.get("image_backend")  # format: "provider_id/model"
    if project_image_backend and "/" in project_image_backend:
        image_provider, image_model = project_image_backend.split("/", 1)
    elif project_image_backend:
        image_provider = _normalize_provider_id(project_image_backend)
        image_model = ""
    else:
        return {}  # No project-level override; use global default
    return {
        "image_provider": image_provider,
        "image_model": image_model,
    }


# ==================== Storyboard image generation ====================


@router.post("/projects/{project_name}/generate/storyboard/{segment_id}")
async def generate_storyboard(
    project_name: str,
    segment_id: str,
    req: GenerateStoryboardRequest,
    _user: CurrentUser,
):
    """
    Submit a storyboard image generation task to the queue and return the task_id immediately.

    Generation is executed asynchronously by GenerationWorker; status is pushed via SSE.
    """
    try:

        def _sync():
            get_project_manager().load_project(project_name)
            script = get_project_manager().load_script(project_name, req.script_file)
            items, id_field, _, _ = get_storyboard_items(script)
            resolved = find_storyboard_item(items, id_field, segment_id)
            if resolved is None:
                raise HTTPException(status_code=404, detail=f"Segment/scene '{segment_id}' does not exist")
            return _snapshot_image_backend(project_name)

        image_snapshot = await asyncio.to_thread(_sync)

        # Validate prompt format
        if isinstance(req.prompt, dict):
            if not is_structured_image_prompt(req.prompt):
                raise HTTPException(
                    status_code=400,
                    detail="prompt must be a string or an object containing scene/composition",
                )
            scene_text = str(req.prompt.get("scene", "")).strip()
            if not scene_text:
                raise HTTPException(status_code=400, detail="prompt.scene cannot be empty")
        elif not isinstance(req.prompt, str):
            raise HTTPException(status_code=400, detail="prompt must be a string or an object")

        # Enqueue
        queue = get_generation_queue()
        result = await queue.enqueue_task(
            project_name=project_name,
            task_type="storyboard",
            media_type="image",
            resource_id=segment_id,
            script_file=req.script_file,
            payload={
                "prompt": req.prompt,
                "script_file": req.script_file,
                **image_snapshot,
            },
            source="webui",
            user_id=_user.id,
        )

        return {
            "success": True,
            "task_id": result["task_id"],
            "message": f"Storyboard generation task for '{segment_id}' has been submitted",
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Video generation ====================


@router.post("/projects/{project_name}/generate/video/{segment_id}")
async def generate_video(project_name: str, segment_id: str, req: GenerateVideoRequest, _user: CurrentUser):
    """
    Submit a video generation task to the queue and return the task_id immediately.

    Requires a storyboard image as the first frame. Generation is executed asynchronously by GenerationWorker.
    """
    try:

        def _sync():
            get_project_manager().load_project(project_name)
            project_path = get_project_manager().get_project_path(project_name)
            storyboard_file = project_path / "storyboards" / f"scene_{segment_id}.png"
            if not storyboard_file.exists():
                raise HTTPException(status_code=400, detail=f"Please generate storyboard image scene_{segment_id}.png first")

        await asyncio.to_thread(_sync)

        # Validate prompt format
        if isinstance(req.prompt, dict):
            if not is_structured_video_prompt(req.prompt):
                raise HTTPException(
                    status_code=400,
                    detail="prompt must be a string or an object containing action/camera_motion",
                )
            action_text = str(req.prompt.get("action", "")).strip()
            if not action_text:
                raise HTTPException(status_code=400, detail="prompt.action cannot be empty")
            dialogue = req.prompt.get("dialogue", [])
            if dialogue is not None and not isinstance(dialogue, list):
                raise HTTPException(status_code=400, detail="prompt.dialogue must be an array")
        elif not isinstance(req.prompt, str):
            raise HTTPException(status_code=400, detail="prompt must be a string or an object")

        # Enqueue (provider is automatically resolved by the service layer from config; callers do not need to pass it)
        queue = get_generation_queue()
        result = await queue.enqueue_task(
            project_name=project_name,
            task_type="video",
            media_type="video",
            resource_id=segment_id,
            script_file=req.script_file,
            payload={
                "prompt": req.prompt,
                "script_file": req.script_file,
                "duration_seconds": req.duration_seconds,
                "seed": req.seed,
            },
            source="webui",
            user_id=_user.id,
        )

        return {
            "success": True,
            "task_id": result["task_id"],
            "message": f"Video generation task for '{segment_id}' has been submitted",
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Character design image generation ====================


@router.post("/projects/{project_name}/generate/character/{char_name}")
async def generate_character(
    project_name: str,
    char_name: str,
    req: GenerateCharacterRequest,
    _user: CurrentUser,
):
    """
    Submit a character design image generation task to the queue and return the task_id immediately.
    """
    try:

        def _sync():
            project = get_project_manager().load_project(project_name)
            if char_name not in project.get("characters", {}):
                raise HTTPException(status_code=404, detail=f"Character '{char_name}' does not exist")
            return _snapshot_image_backend(project_name)

        image_snapshot = await asyncio.to_thread(_sync)

        # Enqueue
        queue = get_generation_queue()
        result = await queue.enqueue_task(
            project_name=project_name,
            task_type="character",
            media_type="image",
            resource_id=char_name,
            payload={
                "prompt": req.prompt,
                **image_snapshot,
            },
            source="webui",
            user_id=_user.id,
        )

        return {
            "success": True,
            "task_id": result["task_id"],
            "message": f"Character design image generation task for '{char_name}' has been submitted",
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Clue design image generation ====================


@router.post("/projects/{project_name}/generate/clue/{clue_name}")
async def generate_clue(project_name: str, clue_name: str, req: GenerateClueRequest, _user: CurrentUser):
    """
    Submit a clue design image generation task to the queue and return the task_id immediately.
    """
    try:

        def _sync():
            project = get_project_manager().load_project(project_name)
            if clue_name not in project.get("clues", {}):
                raise HTTPException(status_code=404, detail=f"Clue '{clue_name}' does not exist")
            return _snapshot_image_backend(project_name)

        image_snapshot = await asyncio.to_thread(_sync)

        # Enqueue
        queue = get_generation_queue()
        result = await queue.enqueue_task(
            project_name=project_name,
            task_type="clue",
            media_type="image",
            resource_id=clue_name,
            payload={
                "prompt": req.prompt,
                **image_snapshot,
            },
            source="webui",
            user_id=_user.id,
        )

        return {
            "success": True,
            "task_id": result["task_id"],
            "message": f"Clue design image generation task for '{clue_name}' has been submitted",
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Request processing failed")
        raise HTTPException(status_code=500, detail=str(e))
