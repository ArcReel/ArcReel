"""Task queue and SSE routes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

from fastapi import APIRouter, Query, Request

from lib.db.repositories.task_repo import TaskNotCancellableError
from lib.edit_timeline.errors import edit_timeline_message
from lib.generation.generation_queue import get_generation_queue
from lib.generation.generation_result import decode_generation_problem
from lib.generation.task_failure import parse_failure, render_failure
from lib.i18n import render_message
from lib.i18n.display_names import DisplayNames
from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError
from lib.project.project_manager import get_project_manager
from server.i18n import DisplayNamesCatalog, Translator
from server.services.project.episode_item_refs import with_episode_item_refs

router = APIRouter()


def get_task_queue():
    return get_generation_queue()


def _render_warnings(warnings: Any, translate: Callable[..., str], names: DisplayNames) -> list[str]:
    """Render ``result.warnings`` ``{key, params}`` entries as text in the current language.

    Skip malformed entries rather than raising: warnings are advisory, and malformed entries must not make the task list return 500.
    ``lib.i18n`` falls back to the key itself for unknown keys, also without raising."""
    if not isinstance(warnings, list):
        return []
    texts: list[str] = []
    for warning in cast(list[Any], warnings):
        if not isinstance(warning, dict):
            continue
        entry = cast(dict[str, Any], warning)
        key = entry.get("key")
        if not isinstance(key, str):
            continue
        params = entry.get("params")
        try:
            text = (
                render_message(key, cast(dict[str, Any], params), translate, names)
                if isinstance(params, dict)
                else translate(key)
            )
        except TypeError:
            # Malformed but valid JSON params, such as reserved "locale" keys, can make the translation call fail;
            # skip that entry rather than returning 500 for the entire task list, matching other tolerant branches here.
            continue
        texts.append(text)
    return texts


def _localize_task(task: dict[str, Any], translate: Callable[..., str], names: DisplayNames) -> dict[str, Any]:
    """Return ``task`` with its stored failure reason and warnings rendered for the request locale.

    Known structured codes become localized text while their machine ``error_code`` and
    ``error_params`` remain available to API consumers, and the stored server text moves to
    ``error_detail``; raw exception text and legacy rows pass through unchanged without
    ``error_detail`` (see ``lib.generation.task_failure.render_failure``). Generation
    warnings stored as ``result.warnings`` (``{key, params}`` entries written by the
    reference-video pipeline) are rendered in place into a list of strings, mirroring
    how ``error_message`` is rendered. Provider and model IDs render as display names from
    ``names`` in the localized text only; ``error_params`` and ``error_detail`` keep them.
    Internal execution checkpoints are stripped at this API serialization boundary. The input dict is never mutated — a rendered copy
    is returned when necessary — so dicts owned by the queue layer stay locale-neutral
    and cannot be polluted across requests.
    """
    localized = (
        {key: value for key, value in task.items() if key != "execution_checkpoint_json"}
        if "execution_checkpoint_json" in task
        else task
    )
    message = localized.get("error_message")
    if message:
        failure = parse_failure(message)
        problem = decode_generation_problem(message)
        if problem is not None:
            params = dict(problem.params)
            issues = params.get("issues")
            if isinstance(issues, list):
                params["units"] = "、".join(
                    dict.fromkeys(
                        str(issue["unit_id"]) for issue in issues if isinstance(issue, dict) and issue.get("unit_id")
                    )
                )
            key = problem.code
            message = edit_timeline_message(problem.code, params)
            if message is not None:
                key, params = message
            translated = render_message(key, params, translate, names)
            localized = {**localized, "error_code": problem.code, "error_params": problem.params}
            if translated != key:
                localized = {**localized, "error_message": translated, "error_detail": problem.detail}
            else:
                localized = {**localized, "error_message": problem.detail}
        elif failure is None:
            localized = {**localized, "error_message": render_failure(message, translate, names)}
        else:
            code, params = failure
            localized = {
                **localized,
                "error_code": code,
                "error_params": params,
                "error_message": render_failure(message, translate, names),
                "error_detail": message,
            }
    result = localized.get("result")
    if isinstance(result, dict):
        result_dict = cast(dict[str, Any], result)
        if result_dict.get("warnings"):
            rendered = _render_warnings(result_dict["warnings"], translate, names)
            localized = {**localized, "result": {**result_dict, "warnings": rendered}}
    return localized


async def _with_resource_refs(tasks: list[dict[str, Any]], translate: Callable[..., str]) -> list[dict[str, Any]]:
    """Attach ``resource_ref`` with the owning episode’s title and broadcast position for UI labels such as “Title · S01”."""
    return await asyncio.to_thread(
        with_episode_item_refs,
        tasks,
        id_field="resource_id",
        ref_field="resource_ref",
        projects=get_project_manager(),
        translate=translate,
    )


@router.get("/tasks/stats")
async def get_task_stats(project_name: str | None = None):
    queue = get_task_queue()
    stats = await queue.get_task_stats(project_name=project_name)
    return {"stats": stats}


@router.get("/tasks")
async def list_tasks(
    _t: Translator,
    names: DisplayNamesCatalog,
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
    result["items"] = await _with_resource_refs(
        [_localize_task(task, _t, names) for task in result.get("items", [])], _t
    )
    return result


@router.get("/projects/{project_name}/tasks")
async def list_project_tasks(
    project_name: str,
    _t: Translator,
    names: DisplayNamesCatalog,
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
    result["items"] = await _with_resource_refs(
        [_localize_task(task, _t, names) for task in result.get("items", [])], _t
    )
    return result


@router.get("/tasks/{task_id}/cancel-preview")
async def cancel_preview(task_id: str, _t: Translator, names: DisplayNamesCatalog):
    queue = get_task_queue()
    try:
        preview = await queue.get_cancel_preview(task_id)
    except TaskNotCancellableError as e:
        raise ConflictError("task_running_not_cancellable", id=task_id) from e
    except ValueError as e:
        raise BadRequestError("task_not_found", id=task_id) from e
    [task, *cascaded] = await _with_resource_refs(
        [_localize_task(task, _t, names) for task in [preview["task"], *preview["cascaded"]]], _t
    )
    return {**preview, "task": task, "cascaded": cascaded}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, _t: Translator, names: DisplayNamesCatalog):
    queue = get_task_queue()
    try:
        result = await queue.cancel_task(task_id)
    except TaskNotCancellableError as e:
        raise ConflictError("task_running_not_cancellable", id=task_id) from e
    except ValueError as e:
        raise BadRequestError("task_not_found", id=task_id) from e
    # Return terminal tasks, including failures, unchanged; error_message shares its source with lists/details/SSE,
    # so localize it to avoid exposing raw [code] {params} at this entry point.
    for key in ("cancelled", "skipped_terminal"):
        result[key] = await _with_resource_refs([_localize_task(task, _t, names) for task in result.get(key, [])], _t)
    return result


@router.post("/tasks/{task_id}/retry-download")
async def retry_artifact_download(task_id: str, request: Request, _t: Translator, names: DisplayNamesCatalog):
    queue = get_task_queue()
    worker = getattr(request.app.state, "generation_worker", None)
    if worker is None:
        raise BadRequestError("task_retry_download_unavailable", id=task_id)
    try:
        # Read the polling timeout before changing state: configuration reads can fail, with no rollback point after commit,
        # leaving the task permanently running, unclaimed by the queue and ineligible for retry-download.
        poll_timeout_seconds = await worker.read_video_poll_timeout_seconds()
        task = await queue.retry_artifact_download(task_id)
        await worker.retry_artifact_download(task, poll_timeout_seconds=poll_timeout_seconds)
    except ValueError as exc:
        raise BadRequestError("task_retry_download_unavailable", id=task_id) from exc
    [task] = await _with_resource_refs([_localize_task(task, _t, names)], _t)
    return {"task": task}


@router.get("/projects/{project_name}/tasks/cancel-all-preview")
async def cancel_all_preview(project_name: str):
    queue = get_task_queue()
    queued_count = await queue.get_cancel_all_preview(project_name)
    return {"queued_count": queued_count}


@router.post("/projects/{project_name}/tasks/cancel-all")
async def cancel_all_queued(project_name: str):
    queue = get_task_queue()
    return await queue.cancel_all_queued(project_name)


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    _t: Translator,
    names: DisplayNamesCatalog,
):
    queue = get_task_queue()
    task = await queue.get_task(task_id)
    if not task:
        raise NotFoundError("task_not_found", id=task_id)
    [localized] = await _with_resource_refs([_localize_task(task, _t, names)], _t)
    return {"task": localized}
