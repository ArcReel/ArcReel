"""
SSE stream for project data changes inside the workspace.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from server.services.project_events import PROJECT_EVENTS_HEARTBEAT_SECONDS, ProjectEventService

router = APIRouter()


def get_project_event_service(request: Request) -> ProjectEventService:
    return request.app.state.project_event_service


def _format_sse(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@router.get("/projects/{project_name}/events/stream")
async def stream_project_events(project_name: str, request: Request):
    service = get_project_event_service(request)
    heartbeat_sec = max(5.0, float(PROJECT_EVENTS_HEARTBEAT_SECONDS))
    try:
        queue, snapshot = await service.subscribe(project_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    async def event_generator():
        last_heartbeat = time.monotonic()
        try:
            yield _format_sse("snapshot", snapshot)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event_name, payload = await asyncio.wait_for(
                        queue.get(),
                        timeout=heartbeat_sec,
                    )
                    yield _format_sse(event_name, payload)
                    last_heartbeat = time.monotonic()
                except asyncio.TimeoutError:
                    if time.monotonic() - last_heartbeat >= heartbeat_sec:
                        yield _format_sse(
                            "heartbeat",
                            {
                                "project_name": project_name,
                                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            },
                        )
                        last_heartbeat = time.monotonic()
        finally:
            await service.unsubscribe(project_name, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
