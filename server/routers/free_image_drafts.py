"""自由生图与项目草稿库。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Path
from fastapi.responses import Response
from pydantic import BaseModel, Field

from lib.config.generation_options import GenerationOptions
from lib.generation.generation_queue import get_generation_queue
from lib.infra.api_errors import BadRequestError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.material.formal_objects import formal_commit
from lib.project.asset_types import ASSET_SPECS, resolve_asset_key
from lib.project.project_manager import get_project_manager
from server.auth import CurrentUser
from server.services.currency.upload_finalize import install_manual_asset_sheet_upload
from server.services.free_image_drafts import delete_draft, list_drafts, read_draft_image

router = APIRouter(prefix="/projects/{project_name}/image-drafts")


DraftId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


class GenerateFreeImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10_000)
    generation_options: GenerationOptions | None = None
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4"] = "1:1"


class BindDraftRequest(BaseModel):
    asset_type: Literal["character", "scene", "prop"]
    asset_name: str = Field(min_length=1, max_length=200)


@router.get("")
async def get_drafts(project_name: str, user: CurrentUser):
    return {"items": await asyncio.to_thread(list_drafts, project_name)}


@router.post("/generate")
async def generate_free_image(project_name: str, req: GenerateFreeImageRequest, user: CurrentUser):
    if not req.prompt.strip():
        raise BadRequestError("prompt_text_empty")
    await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    draft_id = uuid.uuid4().hex
    payload = {
        "prompt": req.prompt.strip(),
        "aspect_ratio": req.aspect_ratio,
        **(
            {"generation_options": req.generation_options.model_dump(exclude_unset=True)}
            if req.generation_options
            else {}
        ),
    }
    result = await get_generation_queue().enqueue_task(
        project_name=project_name,
        task_type="free_image",
        media_type="image",
        resource_id=draft_id,
        payload=payload,
        source="webui",
        user_id=user.id,
    )
    return {"draft_id": draft_id, "task_id": result["task_id"]}


async def _image(project_name: str, draft_id: str) -> bytes:
    try:
        return await asyncio.to_thread(read_draft_image, project_name, draft_id)
    except FileNotFoundError as exc:
        raise NotFoundError("image_draft_not_found") from exc


@router.get("/{draft_id}/image")
async def get_image(project_name: str, draft_id: DraftId, user: CurrentUser):
    return Response(
        await _image(project_name, draft_id), media_type="image/png", headers={"Cache-Control": "private, no-cache"}
    )


@router.delete("/{draft_id}", status_code=204)
async def remove_draft(project_name: str, draft_id: DraftId, user: CurrentUser):
    try:
        await run_sync_transaction(delete_draft, project_name, draft_id)
    except FileNotFoundError as exc:
        raise NotFoundError("image_draft_not_found") from exc
    return Response(status_code=204)


@router.post("/{draft_id}/bind")
async def bind_draft(project_name: str, draft_id: DraftId, req: BindDraftRequest, user: CurrentUser):
    content = await _image(project_name, draft_id)
    pm = get_project_manager()
    project = await asyncio.to_thread(pm.load_project, project_name)
    spec = ASSET_SPECS[req.asset_type]
    key = resolve_asset_key(project.get(spec.bucket_key), req.asset_name)
    if key is None:
        raise NotFoundError("asset_not_found", name=req.asset_name)
    target_rel = f"{spec.subdir}/{key}.png"
    async with formal_commit(project_name):
        installed = await run_sync_transaction(
            install_manual_asset_sheet_upload,
            project_manager=pm,
            project_name=project_name,
            asset_type=req.asset_type,
            name=key,
            sheet_path=target_rel,
            content=content,
            original_filename=f"{draft_id}.png",
            require_asset=True,
        )
    if installed is None:
        raise NotFoundError("asset_not_found", name=key)
    return {"success": True, "file_path": target_rel}
