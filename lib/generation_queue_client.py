"""
Helper utilities for skills to enqueue-and-wait generation tasks.

All public functions are async wrappers around the async GenerationQueue.
Skill scripts that run outside the event loop should use asyncio.run().
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from lib.db.base import DEFAULT_USER_ID
from lib.generation_queue import (
    TASK_WORKER_LEASE_TTL_SEC,
    get_generation_queue,
    read_queue_poll_interval,
)


class WorkerOfflineError(RuntimeError):
    """Raised when queue worker is offline."""


class TaskFailedError(RuntimeError):
    """Raised when queued task finishes as failed."""


class TaskCancelledError(RuntimeError):
    """Raised when queued task is cancelled by user."""


class TaskWaitTimeoutError(TimeoutError):
    """Raised when queued task does not finish before timeout."""


DEFAULT_TASK_WAIT_TIMEOUT_SEC: float | None = 3600.0
DEFAULT_WORKER_OFFLINE_GRACE_SEC: float = max(20.0, float(TASK_WORKER_LEASE_TTL_SEC) * 2.0)


def read_task_wait_timeout() -> float | None:
    value = DEFAULT_TASK_WAIT_TIMEOUT_SEC
    if value is None:
        return None
    value = float(value)
    if value <= 0:
        return None
    return value


def read_worker_offline_grace() -> float:
    return max(1.0, float(DEFAULT_WORKER_OFFLINE_GRACE_SEC))


async def is_worker_online(lease_name: str = "default") -> bool:
    queue = get_generation_queue()
    return await queue.is_worker_online(name=lease_name)


async def wait_for_task(
    task_id: str,
    poll_interval: float | None = None,
    *,
    timeout_seconds: float | None = None,
    lease_name: str = "default",
    worker_offline_grace_seconds: float | None = None,
) -> dict[str, Any]:
    queue = get_generation_queue()
    interval = poll_interval if poll_interval is not None else read_queue_poll_interval()
    timeout = read_task_wait_timeout() if timeout_seconds is None else timeout_seconds
    if timeout is not None:
        timeout = max(0.1, float(timeout))
    offline_grace = (
        read_worker_offline_grace()
        if worker_offline_grace_seconds is None
        else max(0.1, float(worker_offline_grace_seconds))
    )
    start = time.monotonic()
    offline_since: float | None = None

    while True:
        task = await queue.get_task(task_id)
        if not task:
            raise RuntimeError(f"task not found: {task_id}")

        status = task.get("status")
        if status in ("succeeded", "failed", "cancelled"):
            return task

        now = time.monotonic()
        if timeout is not None and now - start >= timeout:
            raise TaskWaitTimeoutError(f"timed out waiting for task '{task_id}' after {timeout:.1f}s")

        if await queue.is_worker_online(name=lease_name):
            offline_since = None
        else:
            if offline_since is None:
                offline_since = now
            elif now - offline_since >= offline_grace:
                raise WorkerOfflineError(f"queue worker offline while waiting for task '{task_id}'")

        await asyncio.sleep(interval)


async def enqueue_and_wait(
    *,
    project_name: str,
    task_type: str,
    media_type: str,
    resource_id: str,
    payload: dict[str, Any] | None = None,
    script_file: str | None = None,
    source: str = "skill",
    lease_name: str = "default",
    wait_timeout_seconds: float | None = None,
    worker_offline_grace_seconds: float | None = None,
    dependency_task_id: str | None = None,
    dependency_group: str | None = None,
    dependency_index: int | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    enqueue_result = await enqueue_task_only(
        project_name=project_name,
        task_type=task_type,
        media_type=media_type,
        resource_id=resource_id,
        payload=payload,
        script_file=script_file,
        source=source,
        lease_name=lease_name,
        dependency_task_id=dependency_task_id,
        dependency_group=dependency_group,
        dependency_index=dependency_index,
        user_id=user_id,
    )

    task = await wait_for_task(
        enqueue_result["task_id"],
        timeout_seconds=wait_timeout_seconds,
        lease_name=lease_name,
        worker_offline_grace_seconds=worker_offline_grace_seconds,
    )
    if task.get("status") == "failed":
        message = task.get("error_message") or "task failed"
        raise TaskFailedError(message)
    if task.get("status") == "cancelled":
        raise TaskCancelledError(f"task '{enqueue_result['task_id']}' was cancelled")

    return {
        "enqueue": enqueue_result,
        "task": task,
        "result": task.get("result") or {},
    }


async def enqueue_task_only(
    *,
    project_name: str,
    task_type: str,
    media_type: str,
    resource_id: str,
    payload: dict[str, Any] | None = None,
    script_file: str | None = None,
    source: str = "skill",
    lease_name: str = "default",
    dependency_task_id: str | None = None,
    dependency_group: str | None = None,
    dependency_index: int | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    queue = get_generation_queue()

    if not await queue.is_worker_online(name=lease_name):
        raise WorkerOfflineError("queue worker is offline")

    enqueue_result = await queue.enqueue_task(
        project_name=project_name,
        task_type=task_type,
        media_type=media_type,
        resource_id=resource_id,
        payload=payload or {},
        script_file=script_file,
        source=source,
        dependency_task_id=dependency_task_id,
        dependency_group=dependency_group,
        dependency_index=dependency_index,
        user_id=user_id,
    )
    return enqueue_result


# ---------------------------------------------------------------------------
# Sync wrappers for skill scripts running outside an event loop
# ---------------------------------------------------------------------------


def _run_in_fresh_loop(coro):
    """Run *coro* with ``asyncio.run()``, disposing stale pool connections first."""
    from lib.db.engine import dispose_pool

    dispose_pool()
    return asyncio.run(coro)


def _run_sync(coro):
    """Run an async coroutine from synchronous code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Already inside an event loop — create a new thread to run the coroutine.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run_in_fresh_loop, coro).result()
    return _run_in_fresh_loop(coro)


def enqueue_task_only_sync(**kwargs) -> dict[str, Any]:
    """Sync wrapper for enqueue_task_only()."""
    return _run_sync(enqueue_task_only(**kwargs))


def wait_for_task_sync(task_id: str, poll_interval=None, **kwargs) -> dict[str, Any]:
    """Sync wrapper for wait_for_task()."""
    return _run_sync(wait_for_task(task_id, poll_interval, **kwargs))


def enqueue_and_wait_sync(**kwargs) -> dict[str, Any]:
    """Sync wrapper for enqueue_and_wait()."""
    return _run_sync(enqueue_and_wait(**kwargs))


# ---------------------------------------------------------------------------
# Batch enqueue-and-wait
# ---------------------------------------------------------------------------


@dataclass
class BatchTaskSpec:
    """Specification for a single task in a batch submission."""

    task_type: str
    media_type: str
    resource_id: str
    payload: dict[str, Any] | None = None
    script_file: str | None = None
    source: str = "skill"
    # Express dependency by resource_id; auto-resolved to task_id during enqueue.
    dependency_resource_id: str | None = None
    dependency_group: str | None = None
    dependency_index: int | None = None


@dataclass
class BatchTaskResult:
    """Result of a single task after batch execution."""

    resource_id: str
    task_id: str
    status: str  # "succeeded" | "failed" | "cancelled"
    result: dict[str, Any] | None = None
    error: str | None = None


def _task_result_from_finished(task: dict[str, Any], resource_id: str, task_id: str) -> BatchTaskResult:
    """Build a BatchTaskResult from a finished task dict."""
    if task.get("status") == "failed":
        return BatchTaskResult(
            resource_id=resource_id,
            task_id=task_id,
            status="failed",
            error=task.get("error_message") or "task failed",
        )
    if task.get("status") == "cancelled":
        return BatchTaskResult(
            resource_id=resource_id,
            task_id=task_id,
            status="cancelled",
            error="task cancelled",
        )
    return BatchTaskResult(
        resource_id=resource_id,
        task_id=task_id,
        status="succeeded",
        result=task.get("result") or {},
    )


async def batch_enqueue_and_wait(
    *,
    project_name: str,
    specs: list[BatchTaskSpec],
    on_success: Callable[[BatchTaskResult], None] | None = None,
    on_failure: Callable[[BatchTaskResult], None] | None = None,
) -> tuple[list[BatchTaskResult], list[BatchTaskResult]]:
    """Async: enqueue sequentially, then gather-wait all tasks.

    Runs entirely within a single event loop, so all asyncpg connections
    are bound to the same loop — no cross-loop errors.

    Returns ``(successes, failures)`` — two lists of ``BatchTaskResult``.
    """
    if not specs:
        return [], []
    # Phase 1 — Sequential enqueue (dependency resolution requires order)
    task_ids: dict[str, str] = {}
    for spec in specs:
        dep_task_id: str | None = None
        if spec.dependency_resource_id:
            dep_task_id = task_ids.get(spec.dependency_resource_id)

        enqueue_result = await enqueue_task_only(
            project_name=project_name,
            task_type=spec.task_type,
            media_type=spec.media_type,
            resource_id=spec.resource_id,
            payload=spec.payload,
            script_file=spec.script_file,
            source=spec.source,
            dependency_task_id=dep_task_id,
            dependency_group=spec.dependency_group,
            dependency_index=spec.dependency_index,
        )
        task_ids[spec.resource_id] = enqueue_result["task_id"]

    # Phase 2 — Parallel wait via asyncio.gather (single event loop)
    async def _wait_one(spec: BatchTaskSpec) -> BatchTaskResult:
        tid = task_ids[spec.resource_id]
        try:
            task = await wait_for_task(tid)
            return _task_result_from_finished(task, spec.resource_id, tid)
        except Exception as exc:
            return BatchTaskResult(
                resource_id=spec.resource_id,
                task_id=tid,
                status="failed",
                error=str(exc),
            )

    results = await asyncio.gather(*[_wait_one(s) for s in specs])

    successes: list[BatchTaskResult] = []
    failures: list[BatchTaskResult] = []
    for br in results:
        if br.status == "succeeded":
            successes.append(br)
            if on_success:
                on_success(br)
        else:
            failures.append(br)
            if on_failure:
                on_failure(br)

    return successes, failures


def batch_enqueue_and_wait_sync(
    *,
    project_name: str,
    specs: list[BatchTaskSpec],
    on_success: Callable[[BatchTaskResult], None] | None = None,
    on_failure: Callable[[BatchTaskResult], None] | None = None,
) -> tuple[list[BatchTaskResult], list[BatchTaskResult]]:
    """Batch-enqueue all tasks then wait for all of them to complete.

    Phase 1 — Sequential enqueue: iterate *specs* and call
    ``enqueue_task_only`` for each.  If a spec has
    *dependency_resource_id*, it is automatically resolved to the task_id
    of a previously enqueued spec with that resource_id.

    Phase 2 — Parallel wait: all ``wait_for_task`` coroutines run
    concurrently via ``asyncio.gather`` in a **single event loop**, so all
    asyncpg connections share the same loop — no cross-loop errors.

    Returns ``(successes, failures)`` — two lists of ``BatchTaskResult``.
    """
    return _run_sync(
        batch_enqueue_and_wait(
            project_name=project_name,
            specs=specs,
            on_success=on_success,
            on_failure=on_failure,
        )
    )
