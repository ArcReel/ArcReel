"""
任务队列与 SSE 路由。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from lib.generation_queue import (
    TASK_SSE_HEARTBEAT_SEC,
    get_generation_queue,
    read_queue_poll_interval,
)


router = APIRouter()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_last_event_id(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return max(0, parsed)


def _format_sse(event: str, data: Any, event_id: Optional[int] = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")

    payload = json.dumps(data, ensure_ascii=False)
    for line in payload.splitlines():
        lines.append(f"data: {line}")

    return "\n".join(lines) + "\n\n"


@router.get("/tasks/stats")
async def get_task_stats(project_name: Optional[str] = None):
    queue = get_generation_queue()
    stats = queue.get_task_stats(project_name=project_name)
    return {"stats": stats}


@router.get("/tasks")
async def list_tasks(
    project_name: Optional[str] = None,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    source: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    queue = get_generation_queue()
    return queue.list_tasks(
        project_name=project_name,
        status=status,
        task_type=task_type,
        source=source,
        page=page,
        page_size=page_size,
    )


@router.get("/projects/{project_name}/tasks")
async def list_project_tasks(
    project_name: str,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    source: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    queue = get_generation_queue()
    return queue.list_tasks(
        project_name=project_name,
        status=status,
        task_type=task_type,
        source=source,
        page=page,
        page_size=page_size,
    )


@router.get("/tasks/stream")
async def stream_tasks(
    request: Request,
    project_name: Optional[str] = None,
    last_event_id: Optional[int] = Query(default=None, ge=0),
    last_event_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
):
    queue = get_generation_queue()
    heartbeat_sec = max(5.0, float(TASK_SSE_HEARTBEAT_SEC))
    poll_interval = read_queue_poll_interval()

    header_last_id = _parse_last_event_id(last_event_header)
    resume_requested = (last_event_id is not None) or (header_last_id is not None)
    cursor = last_event_id if last_event_id is not None else header_last_id
    if cursor is None:
        cursor = 0
    cursor = max(0, int(cursor))

    async def event_generator():
        nonlocal cursor

        latest_event_id = queue.get_latest_event_id(project_name=project_name)
        snapshot_last_event_id = max(cursor, latest_event_id) if resume_requested else latest_event_id
        snapshot = {
            "project_name": project_name,
            "tasks": queue.get_recent_tasks_snapshot(project_name=project_name, limit=1000),
            "stats": queue.get_task_stats(project_name=project_name),
            "last_event_id": snapshot_last_event_id,
            "generated_at": _utc_now_iso(),
        }
        yield _format_sse("snapshot", snapshot)
        if not resume_requested:
            cursor = snapshot_last_event_id

        last_heartbeat = time.monotonic()
        while True:
            if await request.is_disconnected():
                break

            events = queue.get_events_since(
                last_event_id=cursor,
                project_name=project_name,
                limit=200,
            )
            if events:
                for event in events:
                    cursor = int(event["id"])
                    yield _format_sse("task", event, event_id=cursor)
                last_heartbeat = time.monotonic()
                continue

            if time.monotonic() - last_heartbeat >= heartbeat_sec:
                heartbeat = {
                    "last_event_id": cursor,
                    "generated_at": _utc_now_iso(),
                }
                yield _format_sse("heartbeat", heartbeat)
                last_heartbeat = time.monotonic()

            await asyncio.sleep(poll_interval)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    queue = get_generation_queue()
    task = queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 '{task_id}' 不存在")
    return {"task": task}
