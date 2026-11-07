"""
SSE stream for project data changes inside the workspace.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

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
        # Invalid project names (path traversal, etc.) are bad requests, not not-found.
        raise BadRequestError("invalid_project_name", name=project_name) from exc
    return service


@router.get(
    "/projects/{project_name}/events/stream",
    response_class=EventSourceResponse,
)
async def stream_project_events(
    project_name: str,
    request: Request,
    service: ProjectEventService = Depends(_project_events_service),
) -> AsyncIterator[ServerSentEvent]:
    try:
        async with service.stream_events(project_name, idle_timeout=PROJECT_EVENTS_SSE_POLL_SECONDS) as stream:
            async for item in stream:
                # Check disconnection at every iteration start; _idle only ensures wake-up while queues are idle,
                # not the sole disconnection checkpoint; detect disconnects immediately even during continuous high-frequency streams.
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
