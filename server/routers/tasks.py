"""
任务队列与 SSE 路由。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from lib.generation_queue import (
    get_generation_queue,
    read_queue_poll_interval,
)
from server.auth import CurrentUser, CurrentUserFlexible

router = APIRouter()


def get_task_queue():
    return get_generation_queue()


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_last_event_id(value: str | None) -> int | None:
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


def _transform_task_event(raw_event: dict, stats: dict) -> dict:
    """将原始 task_events 行转换为前端期望的 TaskStreamTaskPayload 结构。"""
    event_type = raw_event.get("event_type", "")
    action = "created" if event_type == "queued" else "updated"
    return {
        "action": action,
        "task": raw_event.get("data", {}),
        "stats": stats,
    }


@router.get("/tasks/stats")
async def get_task_stats(_user: CurrentUser, project_name: str | None = None):
    queue = get_task_queue()
    stats = await queue.get_task_stats(project_name=project_name)
    return {"stats": stats}


@router.get("/tasks")
async def list_tasks(
    _user: CurrentUser,
    project_name: str | None = None,
    status: str | None = None,
    task_type: str | None = None,
    source: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    queue = get_task_queue()
    return await queue.list_tasks(
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
    _user: CurrentUser,
    status: str | None = None,
    task_type: str | None = None,
    source: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    queue = get_task_queue()
    return await queue.list_tasks(
        project_name=project_name,
        status=status,
        task_type=task_type,
        source=source,
        page=page,
        page_size=page_size,
    )


@router.get("/tasks/stream", response_class=EventSourceResponse, deprecated=True)
async def stream_tasks(
    request: Request,
    _user: CurrentUserFlexible,
    project_name: str | None = None,
    last_event_id: int | None = Query(default=None, ge=0),
    last_event_header: str | None = Header(default=None, alias="Last-Event-ID"),
) -> AsyncIterator[ServerSentEvent]:
    queue = get_task_queue()
    poll_interval = read_queue_poll_interval()

    header_last_id = _parse_last_event_id(last_event_header)
    resume_requested = (last_event_id is not None) or (header_last_id is not None)
    cursor = last_event_id if last_event_id is not None else header_last_id
    if cursor is None:
        cursor = 0
    cursor = max(0, int(cursor))

    latest_event_id = await queue.get_latest_event_id(project_name=project_name)
    snapshot_last_event_id = max(cursor, latest_event_id) if resume_requested else latest_event_id
    snapshot = {
        "project_name": project_name,
        "tasks": await queue.get_recent_tasks_snapshot(project_name=project_name, limit=1000),
        "stats": await queue.get_task_stats(project_name=project_name),
        "last_event_id": snapshot_last_event_id,
        "generated_at": _utc_now_iso(),
    }
    yield ServerSentEvent(event="snapshot", data=snapshot)
    cursor = snapshot_last_event_id

    while True:
        if await request.is_disconnected():
            break

        events = await queue.get_events_since(
            last_event_id=cursor,
            project_name=project_name,
            limit=200,
        )
        if events:
            batch_stats = await queue.get_task_stats(project_name=project_name)
            for event in events:
                cursor = int(event["id"])
                transformed = _transform_task_event(event, batch_stats)
                yield ServerSentEvent(
                    event="task",
                    data=transformed,
                    id=str(cursor),
                )
            continue

        await asyncio.sleep(poll_interval)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, _user: CurrentUser):
    queue = get_task_queue()
    task = await queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 '{task_id}' 不存在")
    return {"task": task}
