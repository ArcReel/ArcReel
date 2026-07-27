"""
任务队列与 SSE 路由。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Query

from lib.api_errors import BadRequestError, NotFoundError
from lib.generation_queue import get_generation_queue
from lib.i18n import Translator
from lib.task_failure import render_failure
from server.auth import CurrentUser

router = APIRouter()


def get_task_queue():
    return get_generation_queue()


def _localize_task(task: dict[str, Any], translate: Callable[..., str]) -> dict[str, Any]:
    """Return ``task`` with its stored failure reason rendered for the request locale.

    Known structured codes become localized text; raw exception text and legacy
    rows pass through unchanged (see ``lib.task_failure.render_failure``). The input
    dict is never mutated — a rendered copy is returned — so dicts owned by the queue
    layer stay locale-neutral and cannot be polluted across requests.
    """
    message = task.get("error_message")
    if not message:
        return task
    return {**task, "error_message": render_failure(message, translate)}


@router.get("/tasks/stats")
async def get_task_stats(_user: CurrentUser, project_name: str | None = None):
    queue = get_task_queue()
    stats = await queue.get_task_stats(project_name=project_name)
    return {"stats": stats}


@router.get("/tasks")
async def list_tasks(
    _user: CurrentUser,
    _t: Translator,
    project_name: str | None = None,
    status: str | None = None,
    task_type: str | None = None,
    source: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    queue = get_task_queue()
    result = await queue.list_tasks(
        project_name=project_name,
        status=status,
        task_type=task_type,
        source=source,
        page=page,
        page_size=page_size,
    )
    result["items"] = [_localize_task(task, _t) for task in result.get("items", [])]
    return result


@router.get("/projects/{project_name}/tasks")
async def list_project_tasks(
    project_name: str,
    _user: CurrentUser,
    _t: Translator,
    status: str | None = None,
    task_type: str | None = None,
    source: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    queue = get_task_queue()
    result = await queue.list_tasks(
        project_name=project_name,
        status=status,
        task_type=task_type,
        source=source,
        page=page,
        page_size=page_size,
    )
    result["items"] = [_localize_task(task, _t) for task in result.get("items", [])]
    return result


@router.get("/tasks/{task_id}/cancel-preview")
async def cancel_preview(task_id: str, _user: CurrentUser):
    queue = get_task_queue()
    try:
        preview = await queue.get_cancel_preview(task_id)
    except ValueError as e:
        raise BadRequestError("task_not_found", id=task_id) from e
    return preview


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, _user: CurrentUser, _t: Translator):
    queue = get_task_queue()
    try:
        result = await queue.cancel_task(task_id)
    except ValueError as e:
        raise BadRequestError("task_not_found", id=task_id) from e
    # 终态任务（含已失败的）原样回给调用方，其 error_message 与列表/详情/SSE 同源，
    # 不本地化就会在这一个出口泄露裸 [code] {params}。
    for key in ("cancelled", "skipped_terminal"):
        result[key] = [_localize_task(task, _t) for task in result.get(key, [])]
    return result


@router.get("/projects/{project_name}/tasks/cancel-all-preview")
async def cancel_all_preview(project_name: str, _user: CurrentUser):
    queue = get_task_queue()
    queued_count = await queue.get_cancel_all_preview(project_name)
    return {"queued_count": queued_count}


@router.post("/projects/{project_name}/tasks/cancel-all")
async def cancel_all_queued(project_name: str, _user: CurrentUser):
    queue = get_task_queue()
    result = await queue.cancel_all_queued(project_name)
    return result


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    _user: CurrentUser,
    _t: Translator,
):
    queue = get_task_queue()
    task = await queue.get_task(task_id)
    if not task:
        raise NotFoundError("task_not_found", id=task_id)
    return {"task": _localize_task(task, _t)}
