"""
Helper utilities for skills to enqueue-and-wait generation tasks.
"""

from __future__ import annotations

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


def is_worker_online(lease_name: str = "default") -> bool:
    queue = get_generation_queue()
    return queue.is_worker_online(name=lease_name)


def wait_for_task(
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
        task = queue.get_task(task_id)
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

        if queue.is_worker_online(name=lease_name):
            offline_since = None
        else:
            if offline_since is None:
                offline_since = now
            elif now - offline_since >= offline_grace:
                raise WorkerOfflineError(
                    f"queue worker offline while waiting for task '{task_id}'"
                )

        time.sleep(interval)


def enqueue_and_wait(
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
) -> Dict[str, Any]:
    queue = get_generation_queue()

    if not queue.is_worker_online(name=lease_name):
        raise WorkerOfflineError("queue worker is offline")

    enqueue_result = queue.enqueue_task(
        project_name=project_name,
        task_type=task_type,
        media_type=media_type,
        resource_id=resource_id,
        payload=payload or {},
        script_file=script_file,
        source=source,
    )

    task = wait_for_task(
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
