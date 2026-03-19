"""
Helper utilities for skills to enqueue-and-wait generation tasks.

All public functions are async wrappers around the async GenerationQueue.
Skill scripts that run outside the event loop should use asyncio.run().
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from lib.generation_queue import (
    TASK_WORKER_LEASE_TTL_SEC,
    get_generation_queue,
    read_queue_poll_interval,
)


class WorkerOfflineError(RuntimeError):
    """Raised when queue worker is offline."""


class TaskFailedError(RuntimeError):
    """Raised when queued task finishes as failed."""


class TaskWaitTimeoutError(TimeoutError):
    """Raised when queued task does not finish before timeout."""


DEFAULT_TASK_WAIT_TIMEOUT_SEC: Optional[float] = 3600.0
DEFAULT_WORKER_OFFLINE_GRACE_SEC: float = max(
    20.0, float(TASK_WORKER_LEASE_TTL_SEC) * 2.0
)


def read_task_wait_timeout() -> Optional[float]:
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
    poll_interval: Optional[float] = None,
    *,
    timeout_seconds: Optional[float] = None,
    lease_name: str = "default",
    worker_offline_grace_seconds: Optional[float] = None,
) -> Dict[str, Any]:
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
    offline_since: Optional[float] = None

    while True:
        task = await queue.get_task(task_id)
        if not task:
            raise RuntimeError(f"task not found: {task_id}")

        status = task.get("status")
        if status in ("succeeded", "failed"):
            return task

        now = time.monotonic()
        if timeout is not None and now - start >= timeout:
            raise TaskWaitTimeoutError(
                f"timed out waiting for task '{task_id}' after {timeout:.1f}s"
            )

        if await queue.is_worker_online(name=lease_name):
            offline_since = None
        else:
            if offline_since is None:
                offline_since = now
            elif now - offline_since >= offline_grace:
                raise WorkerOfflineError(
                    f"queue worker offline while waiting for task '{task_id}'"
                )

        await asyncio.sleep(interval)


async def enqueue_and_wait(
    *,
    project_name: str,
    task_type: str,
    media_type: str,
    resource_id: str,
    payload: Optional[Dict[str, Any]] = None,
    script_file: Optional[str] = None,
    source: str = "skill",
    lease_name: str = "default",
    wait_timeout_seconds: Optional[float] = None,
    worker_offline_grace_seconds: Optional[float] = None,
    dependency_task_id: Optional[str] = None,
    dependency_group: Optional[str] = None,
    dependency_index: Optional[int] = None,
) -> Dict[str, Any]:
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
    payload: Optional[Dict[str, Any]] = None,
    script_file: Optional[str] = None,
    source: str = "skill",
    lease_name: str = "default",
    dependency_task_id: Optional[str] = None,
    dependency_group: Optional[str] = None,
    dependency_index: Optional[int] = None,
) -> Dict[str, Any]:
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


def enqueue_task_only_sync(**kwargs) -> Dict[str, Any]:
    """Sync wrapper for enqueue_task_only()."""
    return _run_sync(enqueue_task_only(**kwargs))


def wait_for_task_sync(task_id: str, poll_interval=None, **kwargs) -> Dict[str, Any]:
    """Sync wrapper for wait_for_task()."""
    return _run_sync(wait_for_task(task_id, poll_interval, **kwargs))


def enqueue_and_wait_sync(**kwargs) -> Dict[str, Any]:
    """Sync wrapper for enqueue_and_wait()."""
    return _run_sync(enqueue_and_wait(**kwargs))
