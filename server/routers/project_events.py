"""
SSE stream for project data changes inside the workspace.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from server.auth import get_current_user_flexible
from server.services.project_events import ProjectEventService

router = APIRouter()

PROJECT_EVENTS_SSE_POLL_SECONDS = 1.0


def get_project_event_service(request: Request) -> ProjectEventService:
    return request.app.state.project_event_service


async def _project_events_subscription(
    project_name: str,
    request: Request,
) -> tuple[ProjectEventService, asyncio.Queue, dict[str, Any]]:
    service = get_project_event_service(request)
    try:
        queue, snapshot = await service.subscribe(project_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return service, queue, snapshot


@router.get(
    "/projects/{project_name}/events/stream",
    response_class=EventSourceResponse,
)
async def stream_project_events(
    project_name: str,
    request: Request,
    _user: Annotated[dict, Depends(get_current_user_flexible)],
    subscription: tuple[ProjectEventService, asyncio.Queue, dict[str, Any]] = Depends(
        _project_events_subscription
    ),
) -> AsyncIterator[ServerSentEvent]:
    service, queue, snapshot = subscription

    try:
        yield ServerSentEvent(event="snapshot", data=snapshot)

        while True:
            if await request.is_disconnected():
                break
            try:
                event_name, payload = await asyncio.wait_for(
                    queue.get(),
                    timeout=PROJECT_EVENTS_SSE_POLL_SECONDS,
                )
            except asyncio.TimeoutError:
                continue
            yield ServerSentEvent(event=event_name, data=payload)
    finally:
        await service.unsubscribe(project_name, queue)
