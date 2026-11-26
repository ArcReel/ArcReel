"""
SSE stream for project data changes inside the workspace.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from lib.infra.api_errors import BadRequestError, NotFoundError
from server.services.project.project_events import ProjectEventService

logger = logging.getLogger(__name__)

router = APIRouter()

PROJECT_EVENTS_SSE_POLL_SECONDS = 1.0


def get_project_event_service(request: Request) -> ProjectEventService:
    return request.app.state.project_event_service


async def _project_events_service(
    project_name: str,
    request: Request,
) -> ProjectEventService:
    """Resolve the service and validate the project exists before streaming starts.

    The 404 must be raised here (before the EventSourceResponse begins) — once the
    stream is open, no HTTP status can be returned.
    """
    service = get_project_event_service(request)
    try:
        await asyncio.to_thread(service.pm.get_project_path, project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except ValueError as exc:
        raise BadRequestError("invalid_project_name", name=project_name) from exc
    return service


class ItemLocationBatch(BaseModel):
    item_ids: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(max_length=200)


class ItemLocation(BaseModel):
    episode: int | None
    item_id: str
    position: int | None
    status: Literal["found", "deleted", "ambiguous", "unloaded"]


class ItemLocations(BaseModel):
    items: list[ItemLocation]


@router.post("/projects/{project_name}/item-locations", response_model=ItemLocations)
async def locate_project_items(
    project_name: str, batch: ItemLocationBatch, service: ProjectEventService = Depends(_project_events_service)
):
    return {"items": service.locate_items(project_name, batch.item_ids)}


@router.get(
    "/projects/{project_name}/events/stream",
    response_class=EventSourceResponse,
)
async def stream_project_events(
    project_name: str,
    request: Request,
    service: ProjectEventService = Depends(_project_events_service),
    episode: Annotated[int | None, Header(alias="X-ArcReel-Active-Episode", ge=1)] = None,
) -> AsyncIterator[ServerSentEvent]:
    try:
        async with service.stream_events(
            project_name, idle_timeout=PROJECT_EVENTS_SSE_POLL_SECONDS, episode=episode
        ) as stream:
            async for item in stream:
                if await request.is_disconnected():
                    break
                if isinstance(item, dict) and item.get("type") == "_idle":
                    continue
                event_name, payload = item
                yield ServerSentEvent(event=event_name, data=payload)
    except FileNotFoundError:
        # Race: project deleted between the Depends check and stream start. The
        # EventSourceResponse has already begun, so we cannot raise HTTP — log and
        # close the stream cleanly.
        logger.info("项目在订阅前被删除，关闭事件流: %s", project_name)
        return
