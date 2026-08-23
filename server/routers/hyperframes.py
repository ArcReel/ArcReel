"""Project-scoped HyperFrames authoring endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel

from lib.project_manager import get_project_manager
from server.services.hyperframes_workspace import (
    HyperframesStudioUnavailable,
    HyperframesWorkspaceService,
    HyperframesWorkspaceUnavailable,
    get_hyperframes_studio_manager,
)
from server.services.presentation_read_model import PresentationUnavailableError

router = APIRouter()
EpisodeNumber = Annotated[int, Path(ge=1)]


class PrepareHyperframesRequest(BaseModel):
    narration_delivery: Literal["post_production", "use_tts"] = "post_production"


def get_hyperframes_workspace_service() -> HyperframesWorkspaceService:
    return HyperframesWorkspaceService(get_project_manager())


def _payload(workspace, *, studio_url: str | None = None) -> dict[str, object]:
    return {
        **workspace.to_dict(),
        "studio_status": "ready" if studio_url else "stopped",
        "studio_url": studio_url,
    }


def _browser_origin(request: Request) -> str:
    """Preserve the browser host when a Vite/reverse proxy rewrites Host upstream."""

    origin = request.headers.get("origin", "").strip()
    return origin if origin and origin != "null" else str(request.base_url)


@router.get("/projects/{project_name}/hyperframes/episodes/{episode}")
async def get_hyperframes_workspace(project_name: str, episode: EpisodeNumber):
    workspace = get_hyperframes_workspace_service().status(project_name, episode)
    if workspace is None:
        return {
            "project_name": project_name,
            "episode": episode,
            "exists": False,
            "workspace_path": None,
            "composition_path": None,
            "manifest_path": None,
            "studio_status": "stopped",
            "studio_url": None,
        }
    return _payload(workspace)


@router.post("/projects/{project_name}/hyperframes/episodes/{episode}")
async def prepare_hyperframes_workspace(
    project_name: str,
    episode: EpisodeNumber,
    payload: PrepareHyperframesRequest,
    request: Request,
):
    service = get_hyperframes_workspace_service()
    try:
        workspace = await service.prepare(
            project_name,
            episode,
            variant=payload.narration_delivery,
        )
        manager = get_hyperframes_studio_manager()
        port = await manager.ensure_started(workspace.path)
        studio_url = manager.public_url(port, _browser_origin(request))
    except (HyperframesWorkspaceUnavailable, PresentationUnavailableError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HyperframesStudioUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _payload(workspace, studio_url=studio_url)


@router.post("/projects/{project_name}/hyperframes/episodes/{episode}/studio")
async def start_hyperframes_studio(project_name: str, episode: EpisodeNumber, request: Request):
    workspace = get_hyperframes_workspace_service().status(project_name, episode)
    if workspace is None:
        raise HTTPException(status_code=404, detail="HyperFrames workspace does not exist")
    try:
        manager = get_hyperframes_studio_manager()
        port = await manager.ensure_started(workspace.path)
        studio_url = manager.public_url(port, _browser_origin(request))
    except HyperframesStudioUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _payload(workspace, studio_url=studio_url)


__all__ = ["get_hyperframes_workspace_service", "router"]
