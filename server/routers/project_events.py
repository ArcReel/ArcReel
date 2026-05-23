"""
SSE stream for project data changes inside the workspace.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from server.auth import CurrentUserFlexible
from server.services.project_events import ProjectEventService

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
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        # 非法项目名(路径穿越等)是坏请求,不是「不存在」。
        raise HTTPException(status_code=400, detail=str(exc))
    return service


@router.get(
    "/projects/{project_name}/events/stream",
    response_class=EventSourceResponse,
)
async def stream_project_events(
    project_name: str,
    request: Request,
    _user: CurrentUserFlexible,
    service: ProjectEventService = Depends(_project_events_service),
) -> AsyncIterator[ServerSentEvent]:
    try:
        async with service.stream_events(project_name, idle_timeout=PROJECT_EVENTS_SSE_POLL_SECONDS) as stream:
            async for item in stream:
                # 每轮迭代顶部都查断线;_idle 仅作为「队列空闲时也要醒一次」的唤醒兜底,
                # 不再独占断线检测的时机——持续高频事件流下断线一样能立刻发现。
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
