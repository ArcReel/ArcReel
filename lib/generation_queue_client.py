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
from lib.prompt_utils import is_structured_image_prompt, is_structured_video_prompt


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

    # image_edit 跨四类资产 + storyboard 共用 task_type，resource_id 命名空间不互斥
    # （角色和道具可能同名）；顶层 resource_type 是去重/占用匹配键的一部分（见
    # lib/generation_queue.py::enqueue_task），从 payload.resource_type 回填而非要求
    # 每个调用方重复传参——路由层的直接 queue.enqueue_task() 调用已显式传入，这里补齐
    # 经 enqueue_task_only()/enqueue_and_wait()/batch_enqueue_and_wait() 入队的路径
    # （目前是 SDK edit_images 工具），避免同名跨类型资源被误判为重复任务。
    raw_resource_type = (payload or {}).get("resource_type")
    resource_type = raw_resource_type if isinstance(raw_resource_type, str) and raw_resource_type else None

    enqueue_result = await queue.enqueue_task(
        project_name=project_name,
        task_type=task_type,
        media_type=media_type,
        resource_id=resource_id,
        payload=payload or {},
        script_file=script_file,
        resource_type=resource_type,
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


# Task types whose prompt is a *video* prompt (string or structured action object);
# everything else carries an *image* prompt (string or structured scene object), and
# plain-string-only asset prompts still flow through the image branch (no scene key).
_VIDEO_TASK_TYPES = frozenset({"video"})
_IMAGE_STRUCTURED_TASK_TYPES = frozenset({"storyboard"})
# 旁白合成任务：文本默认由执行层从剧本 novel_text 读取，prompt 允许缺省；
# 显式传入时必须是非空字符串（作为待合成文本覆盖）。
_TTS_TASK_TYPES = frozenset({"tts"})


def _validate_prompt(task_type: str, prompt: str | dict[str, Any] | None) -> None:
    """Structural prompt validation, provider-agnostic and keyed by task type.

    Mirrors (and now owns) the rules that previously lived inline in the WebUI
    routes, so WebUI and SDK enqueue paths can't diverge. Raises
    :class:`TaskSpecValidationError` with an i18n message code on failure.
    """
    if task_type in _VIDEO_TASK_TYPES:
        if isinstance(prompt, dict):
            if not is_structured_video_prompt(prompt):
                raise TaskSpecValidationError("video_prompt_must_be_string_or_action_object")
            # ``or ""`` 而非默认参数：``{"action": null}`` 时 get 返回 None，
            # ``str(None)`` 会得到 truthy 的 "None" 字符串绕过空值校验。
            if not str(prompt.get("action") or "").strip():
                raise TaskSpecValidationError("video_prompt_action_empty")
            dialogue = prompt.get("dialogue")
            if dialogue is not None and not isinstance(dialogue, list):
                raise TaskSpecValidationError("video_prompt_dialogue_array")
        elif isinstance(prompt, str):
            if not prompt.strip():
                raise TaskSpecValidationError("prompt_text_empty")
        else:
            raise TaskSpecValidationError("prompt_must_be_string_or_object")
        return

    if task_type in _TTS_TASK_TYPES:
        if prompt is None:
            return
        if isinstance(prompt, str):
            if not prompt.strip():
                raise TaskSpecValidationError("prompt_text_empty")
            return
        raise TaskSpecValidationError("tts_prompt_must_be_string_or_null")

    if task_type in _IMAGE_STRUCTURED_TASK_TYPES and isinstance(prompt, dict):
        if not is_structured_image_prompt(prompt):
            raise TaskSpecValidationError("prompt_must_be_string_or_scene_object")
        if not str(prompt.get("scene") or "").strip():
            raise TaskSpecValidationError("prompt_scene_empty")
        return

    # Asset (character/scene/prop) and string-form storyboard prompts: non-empty string.
    if isinstance(prompt, str):
        if not prompt.strip():
            raise TaskSpecValidationError("prompt_text_empty")
        return
    raise TaskSpecValidationError("prompt_must_be_string_or_object")


class TaskSpecValidationError(ValueError):
    """Raised when a request fails the structural validation in ``TaskSpec.from_request``.

    Carries an i18n message *code* (matching keys in the ``errors`` namespace) plus
    optional format params, so routers can translate it to a 4xx without re-deriving
    which rule failed. Agent-facing callers may just render ``str(self)``.
    """

    def __init__(self, code: str, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass
class TaskSpec:
    """Specification for a single enqueue request (single-task or batch member).

    Construct via :meth:`from_request`, the single guard point that owns a request's
    structural validity. Validation is provider-agnostic (no provider fields here):
    capability checks such as ``duration ↔ supported_durations`` live at the execution
    layer, after provider resolution (see ADR-0001).
    """

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

    @classmethod
    def from_request(
        cls,
        *,
        task_type: str,
        media_type: str,
        resource_id: str,
        prompt: str | dict[str, Any] | None = None,
        script_file: str | None = None,
        source: str = "skill",
        extra_payload: dict[str, Any] | None = None,
        dependency_resource_id: str | None = None,
        dependency_group: str | None = None,
        dependency_index: int | None = None,
    ) -> TaskSpec:
        """Validate a request structurally and build a :class:`TaskSpec`.

        Single source of truth for "is this enqueue request structurally legal" —
        both the WebUI routes and the SDK spec builders construct through here so the
        rules can't diverge. Raises :class:`TaskSpecValidationError` on invalid input.
        """
        if not resource_id:
            raise ValueError("resource_id is required")
        _validate_prompt(task_type, prompt)

        # extra_payload 不得携带守卫点已校验的保留键，否则调用方能绕过单一守卫点
        # 把未校验的 prompt / script_file 入队。
        reserved = {"prompt", "script_file"}
        # tts 执行层读 payload.text 优先于 prompt（历史任务排空通道）；新入队一律走
        # 受校验的 prompt，不允许借 extra_payload.text 绕过结构校验。
        if task_type in _TTS_TASK_TYPES:
            reserved = reserved | {"text"}
        if extra_payload and (conflict := reserved & extra_payload.keys()):
            raise ValueError(f"extra_payload contains reserved keys: {', '.join(sorted(conflict))}")

        payload: dict[str, Any] = dict(extra_payload) if extra_payload else {}
        payload["prompt"] = prompt
        if script_file is not None:
            payload["script_file"] = script_file

        return cls(
            task_type=task_type,
            media_type=media_type,
            resource_id=resource_id,
            payload=payload,
            script_file=script_file,
            source=source,
            dependency_resource_id=dependency_resource_id,
            dependency_group=dependency_group,
            dependency_index=dependency_index,
        )


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
    specs: list[TaskSpec],
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
    async def _wait_one(spec: TaskSpec) -> BatchTaskResult:
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
    specs: list[TaskSpec],
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
