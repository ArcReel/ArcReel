"""
Helper utilities for skills to enqueue-and-wait generation tasks.

All public functions are async wrappers around the async GenerationQueue.
Skill scripts that run outside the event loop should use asyncio.run().
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from lib.db.base import DEFAULT_USER_ID
from lib.generation.generation_batch import (
    GenerationBatchBlockedItem,
    GenerationBatchReadModel,
    GenerationBatchRequestSnapshot,
)
from lib.generation.generation_queue import (
    TASK_WORKER_LEASE_TTL_SEC,
    GenerationQueue,
    cleanup_fresh_generation_batch,
    get_generation_queue,
    read_queue_poll_interval,
)
from lib.project.asset_derivatives import DERIVATIVE_TASK_TYPE
from lib.project.asset_types import ASSET_SPECS
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE, resource_id_segments
from lib.prompts.prompt_utils import (
    is_structured_image_prompt,
    is_structured_video_prompt,
    require_storyboard_scene,
)

logger = logging.getLogger(__name__)


class WorkerOfflineError(RuntimeError):
    """Raised when queue worker is offline."""


class TaskFailedError(RuntimeError):
    """Raised when queued task finishes as failed."""


class TaskCancelledError(RuntimeError):
    """Raised when queued task is cancelled by user."""


class TaskWaitTimeoutError(TimeoutError):
    """Raised when queued task does not finish before timeout."""


def is_interrupted_wait_error(exc: BaseException) -> bool:
    """True when *exc* means a task wait was cut short, not provider-judged failed.

    Shared by every ``wait_for_task`` caller — the batch path below and any direct
    per-task wait loop — so a queued/running task caught mid-flight by a timeout or
    an offline worker is reported as interrupted rather than terminal-failed. Reporting
    it failed would suggest a safe retry while the original submission may still land,
    risking a duplicate paid generation.
    """
    return isinstance(exc, (TaskWaitTimeoutError, WorkerOfflineError))


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
    queue: GenerationQueue | None = None,
) -> dict[str, Any]:
    queue = queue or get_generation_queue()
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
    batch_id: str | None = None,
    batch_unit_id: str | None = None,
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
        batch_id=batch_id,
        batch_unit_id=batch_unit_id,
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
    batch_id: str | None = None,
    batch_unit_id: str | None = None,
    batch_unit_ids: tuple[str, ...] = (),
    queue: GenerationQueue | None = None,
) -> dict[str, Any]:
    queue = queue or get_generation_queue()

    if not await queue.is_worker_online(name=lease_name):
        raise WorkerOfflineError("queue worker is offline")

    # image_edit shares task_type across four assets/storyboards with overlapping resource_id names,
    # such as same-name characters/props. resource_type participates in dedup/ownership
    # (lib/generation/generation_queue.py::enqueue_task), filled from payload rather than
    # duplicated caller arguments. Direct router calls already pass it; fill paths through
    # enqueue_task_only/enqueue_and_wait/batch_enqueue_and_wait,
    # currently SDK edit_images, preventing cross-kind names falsely deduplicating.
    raw_resource_type = (payload or {}).get("resource_type")
    resource_type = raw_resource_type if isinstance(raw_resource_type, str) and raw_resource_type else None

    return await queue.enqueue_task(
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
        batch_id=batch_id,
        batch_unit_id=batch_unit_id,
        batch_unit_ids=batch_unit_ids,
    )


async def get_active_tasks_for_resources(
    *,
    project_name: str,
    task_type: str,
    resource_ids: list[str],
    script_file: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    queue: GenerationQueue | None = None,
) -> list[dict[str, Any]]:
    """Find active queued/running tasks matching enqueue deduplication keys.

    Match ``GenerationQueue.enqueue_task`` unique-index semantics, ``resource_type``
    fixed None because callers are not image_edit. Presubmission conflict probes/rejections
    neither consume nor change task deduplication states.
    """
    queue = queue or get_generation_queue()
    return await queue.get_active_tasks_for_resources(
        project_name=project_name,
        task_type=task_type,
        resource_ids=resource_ids,
        script_file=script_file,
        user_id=user_id,
    )


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
# Narration synthesis reads script novel_text by default; prompt may be absent,
# but explicit overrides require nonempty strings.
_TTS_TASK_TYPES = frozenset({"tts"})
# Asset-image descriptions come from execution-time project entries, no enqueue prompts/snapshots.
_ASSET_SHEET_TASK_TYPES = frozenset(ASSET_SPECS)


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
            # ``or ""`` rather than defaults: ``{"action": null}`` returns None,
            # whose ``str(None)`` truthy "None" would bypass empty checks.
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

    if task_type in _ASSET_SHEET_TASK_TYPES:
        if prompt is not None:
            raise ValueError(f"{task_type} tasks read the stored description and take no prompt")
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
        # Share scene predicates with rendering exits, identical enqueue/execution acceptance;
        # no normalized enqueue snapshots, reproject script_file at execution.
        try:
            require_storyboard_scene(prompt)
        except ValueError as exc:
            raise TaskSpecValidationError("prompt_scene_empty") from exc
        return

    # String-form storyboard and image-edit prompts: non-empty string.
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


#: Hierarchical resource IDs→segment counts from persisted shapes: derivative IDs
#: ``本体名/衍生名`` map ``characters/derivatives/{本体}/{衍生}.png``, with each segment
#: independently valid asset names.
_HIERARCHICAL_RESOURCE_ID_SEGMENTS: dict[str, int] = {
    DERIVATIVE_TASK_TYPE: resource_id_segments(CHARACTER_DERIVATIVE_RESOURCE_TYPE)
}


def _expected_resource_id_segments(task_type: str, extra_payload: Mapping[str, Any] | None) -> int:
    """Expected resource_id segment counts.

    ``image_edit`` shares task_type across resources identified by ``payload.resource_type``;
    other task types directly identify resources.
    """
    resource = str((extra_payload or {}).get("resource_type") or "") if task_type == "image_edit" else task_type
    return _HIERARCHICAL_RESOURCE_ID_SEGMENTS.get(resource, 1)


@dataclass
class TaskSpec:
    """Specification for a single enqueue request (single-task or batch member).

    Construct via :meth:`from_request`, the single guard point that owns a request's
    structural validity. Validation is provider-agnostic (no provider fields here):
    capability checks such as ``duration ↔ supported_durations`` live in each route's
    provider-aware request projection and are rechecked when execution materializes the request.
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
    unit_id: str | None = None
    batch_unit_ids: tuple[str, ...] = ()

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
        unit_id: str | None = None,
        batch_unit_ids: tuple[str, ...] = (),
    ) -> TaskSpec:
        """Validate a request structurally and build a :class:`TaskSpec`.

        Single source of truth for "is this enqueue request structurally legal" —
        both the WebUI routes and the SDK spec builders construct through here so the
        rules can't diverge. Raises :class:`TaskSpecValidationError` on invalid input.
        """
        # Whitespace-only IDs are invalid like empty IDs despite passing nonempty character guards;
        # execution would produce whitespace filenames. Central strip-based emptiness avoids per-caller duplication.
        if not resource_id.strip():
            raise TaskSpecValidationError("resource_id_required")
        # resource_id enters artifact paths; separators/.. rejected only by worker safe_join would be too late,
        # after healthy batch members already execute/bill. Reject here as per-unit admission problems,
        # matching other structural failures. Hierarchical IDs validate fixed segment counts/each segment
        # without relaxing separator restrictions inside segments.
        expected_segments = _expected_resource_id_segments(task_type, extra_payload)
        segments = resource_id.split("/") if expected_segments > 1 else [resource_id]
        if len(segments) != expected_segments or any(
            not segment.strip() or any(sep in segment for sep in ("/", "\\", "\x00")) or segment.strip(".") == ""
            for segment in segments
        ):
            raise TaskSpecValidationError("invalid_resource_id", resource_id=resource_id)
        _validate_prompt(task_type, prompt)

        # extra_payload cannot override reserved validated keys, bypassing sole guards
        # for prompt/script_file.
        reserved = {"prompt", "script_file"}
        # TTS reads payload.text before prompt for historical draining; new tasks use validated
        # prompts only, never extra_payload.text validation bypasses.
        if task_type in _TTS_TASK_TYPES:
            reserved = reserved | {"text"}
        if extra_payload and (conflict := reserved & extra_payload.keys()):
            raise ValueError(f"extra_payload contains reserved keys: {', '.join(sorted(conflict))}")

        payload: dict[str, Any] = dict(extra_payload) if extra_payload else {}
        # reference_video prompt is mutable script content; structural guards inspect current text, but tasks retain
        # unit locations/options only; workers reread latest shots by script_file/resource_id.
        if task_type != "reference_video" and task_type not in _ASSET_SHEET_TASK_TYPES:
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
            unit_id=unit_id,
            batch_unit_ids=batch_unit_ids,
        )


@dataclass
class BatchTaskResult:
    """Result of a single task after batch execution.

    ``task`` carries the finished task row so callers can report the provider
    submission checkpoint separately from the queue status; it is ``None`` only
    when the wait itself failed and no terminal row was ever read.

    ``enqueue_interrupted`` marks a target that never reached the queue because
    the sequential enqueue stopped earlier in the batch. It is reported apart
    from an ordinary enqueue failure: nothing about this target itself was
    refused, so the follow-up is simply to queue it again.
    """

    resource_id: str
    task_id: str
    status: str  # "succeeded" | "failed" | "cancelled" | "interrupted"
    result: dict[str, Any] | None = None
    error: str | None = None
    task: dict[str, Any] | None = None
    enqueue_interrupted: bool = False


def _task_result_from_finished(task: dict[str, Any], resource_id: str, task_id: str) -> BatchTaskResult:
    """Build a BatchTaskResult from a finished task dict."""
    if task.get("status") == "failed":
        return BatchTaskResult(
            resource_id=resource_id,
            task_id=task_id,
            status="failed",
            error=task.get("error_message") or "task failed",
            task=task,
        )
    if task.get("status") == "cancelled":
        return BatchTaskResult(
            resource_id=resource_id,
            task_id=task_id,
            status="cancelled",
            error="task cancelled",
            task=task,
        )
    return BatchTaskResult(
        resource_id=resource_id,
        task_id=task_id,
        status="succeeded",
        result=task.get("result") or {},
        task=task,
    )


@dataclass(frozen=True, slots=True)
class EnqueuedTask:
    """One queued member of a batch, and whether this call is what created it."""

    resource_id: str
    task_id: str
    deduped: bool
    unit_id: str | None = None


async def _enqueue_sequentially(
    *,
    project_name: str,
    specs: list[TaskSpec],
    stop_on_failure: bool,
    user_id: str = DEFAULT_USER_ID,
    batch_id: str | None = None,
    queue: GenerationQueue | None = None,
) -> tuple[list[EnqueuedTask], list[BatchTaskResult]]:
    """Queue *specs* in order, resolving each dependency against its predecessor.

    One spec's enqueue failure never takes down what the loop already created:
    a queued task is a fully admitted, billable unit and runs to completion. Each
    target that did not reach the queue is captured as its own
    ``BatchTaskResult`` (``task_id=""`` marks "never queued", distinct from a
    queued task that later failed) so every requested ID ends up accounted for
    and the next missing-only sweep re-creates exactly those.

    Without ``stop_on_failure`` every spec still gets its own queue attempt.
    With it, the first failure ends the loop and the remaining specs are reported
    as interrupted — a repeated systemic failure (queue or DB down) would
    otherwise be retried once per remaining member.
    """

    enqueued: list[EnqueuedTask] = []
    task_ids: dict[str, str] = {}
    failures: list[BatchTaskResult] = []
    failed_resource_ids: set[str] = set()
    for index, spec in enumerate(specs):
        if spec.dependency_resource_id and spec.dependency_resource_id in failed_resource_ids:
            failures.append(
                BatchTaskResult(
                    resource_id=spec.unit_id or spec.resource_id,
                    task_id="",
                    status="failed",
                    error=f"dependency {spec.dependency_resource_id} failed to enqueue",
                )
            )
            failed_resource_ids.add(spec.resource_id)
            continue
        dep_task_id: str | None = None
        if spec.dependency_resource_id:
            dep_task_id = task_ids.get(spec.dependency_resource_id)

        try:
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
                user_id=user_id,
                batch_id=batch_id,
                batch_unit_ids=(
                    tuple(dict.fromkeys((spec.unit_id or spec.resource_id, *spec.batch_unit_ids)))
                    if batch_id is not None
                    else ()
                ),
                queue=queue,
            )
        except Exception as exc:
            failures.append(
                BatchTaskResult(
                    resource_id=spec.unit_id or spec.resource_id,
                    task_id="",
                    status="failed",
                    error=str(exc),
                    enqueue_interrupted=stop_on_failure,
                )
            )
            failed_resource_ids.add(spec.resource_id)
            if stop_on_failure:
                failures.extend(
                    BatchTaskResult(
                        resource_id=pending.unit_id or pending.resource_id,
                        task_id="",
                        status="failed",
                        # detail and both ``enqueue_problem`` defaults are English contract fields
                        # for Agents/diagnostics; endpoints localize user text through problem codes.
                        error=f"batch enqueue stopped at '{spec.resource_id}'; this target was never queued",
                        enqueue_interrupted=True,
                    )
                    for pending in specs[index + 1 :]
                )
                break
            continue
        deduped = bool(enqueue_result.get("deduped"))
        task_ids[spec.resource_id] = enqueue_result["task_id"]
        enqueued.append(
            EnqueuedTask(
                resource_id=spec.resource_id,
                task_id=enqueue_result["task_id"],
                deduped=deduped,
                unit_id=spec.unit_id,
            )
        )
    return enqueued, failures


async def batch_enqueue_only(
    *,
    project_name: str,
    specs: list[TaskSpec],
    user_id: str = DEFAULT_USER_ID,
    batch_id: str | None = None,
    queue: GenerationQueue | None = None,
) -> tuple[list[EnqueuedTask], list[BatchTaskResult]]:
    """Create the batch's tasks without waiting for their results.

    The entry that already admitted the batch as a whole uses this. Tasks
    created before an interruption keep running — they are complete, paid-for
    units — and the targets that never reached the queue come back as failures
    for the caller to report per ID.
    """

    return await _enqueue_sequentially(
        project_name=project_name,
        specs=specs,
        stop_on_failure=True,
        user_id=user_id,
        batch_id=batch_id,
        queue=queue,
    )


async def submit_generation_batch(
    *,
    project_name: str,
    operation: str,
    requested: GenerationBatchRequestSnapshot,
    blocked: list[GenerationBatchBlockedItem],
    specs: list[TaskSpec],
    source: str,
    user_id: str = DEFAULT_USER_ID,
    queue: GenerationQueue | None = None,
) -> tuple[GenerationBatchReadModel, list[EnqueuedTask], list[BatchTaskResult]]:
    """Persist one admission result, enqueue its members, and return the durable read model."""

    queue = queue or get_generation_queue()
    await queue.assert_project_migration_ok(project_name)
    batch_id = await queue.create_generation_batch(
        project_name=project_name,
        operation=operation,
        requested=requested,
        blocked=blocked,
        source=source,
        user_id=user_id,
    )
    try:
        enqueued, failures = await batch_enqueue_only(
            project_name=project_name,
            specs=specs,
            user_id=user_id,
            batch_id=batch_id,
            queue=queue,
        )
        return (
            await queue.get_generation_batch(project_name=project_name, batch_id=batch_id, user_id=user_id),
            enqueued,
            failures,
        )
    except BaseException as failure:
        await cleanup_fresh_generation_batch(
            queue,
            project_name=project_name,
            batch_id=batch_id,
            user_id=user_id,
            failure=failure,
        )
        raise


async def batch_enqueue_and_wait(
    *,
    project_name: str,
    specs: list[TaskSpec],
    on_success: Callable[[BatchTaskResult], None] | None = None,
    on_failure: Callable[[BatchTaskResult], None] | None = None,
    stop_on_failure: bool = False,
    batch_id: str | None = None,
    queue: GenerationQueue | None = None,
    user_id: str = DEFAULT_USER_ID,
    on_enqueued: Callable[[], None] | None = None,
) -> tuple[list[BatchTaskResult], list[BatchTaskResult]]:
    """Async: enqueue sequentially, then gather-wait all tasks.

    Runs entirely within a single event loop, so all asyncpg connections
    are bound to the same loop — no cross-loop errors.

    ``stop_on_failure`` ends the sequential enqueue at the first spec that
    cannot be queued. Whatever it already created keeps running; the targets
    that never reached the queue come back among the failures as interrupted.

    ``on_enqueued`` runs after the enqueue phase and before any waiting; an empty
    ``specs`` returns without calling it.

    Returns ``(successes, failures)`` — two lists of ``BatchTaskResult``.
    """
    if not specs:
        return [], []
    # Phase 1 — Sequential enqueue (dependency resolution requires order).
    enqueued, enqueue_failures = await _enqueue_sequentially(
        project_name=project_name,
        specs=specs,
        stop_on_failure=stop_on_failure,
        user_id=user_id,
        batch_id=batch_id,
        queue=queue,
    )
    if on_enqueued is not None:
        on_enqueued()
    task_ids = {item.unit_id or item.resource_id: item.task_id for item in enqueued}

    # Phase 2 — Parallel wait via asyncio.gather (single event loop), only for
    # specs that actually reached the queue.
    enqueued_specs = [spec for spec in specs if (spec.unit_id or spec.resource_id) in task_ids]

    async def _wait_one(spec: TaskSpec) -> BatchTaskResult:
        unit_id = spec.unit_id or spec.resource_id
        tid = task_ids[unit_id]
        try:
            task = await wait_for_task(tid, queue=queue)
            return _task_result_from_finished(task, unit_id, tid)
        except (TaskWaitTimeoutError, WorkerOfflineError) as exc:
            # wait_for_task confirmed nonterminal state immediately before raising,
            # meaning interrupted waits, not provider failure. Separate status lets
            # record_batch_outcomes report INTERRUPTED, not FAILED, preventing blind retry
            # of still-successful tasks and duplicate paid submissions.
            return BatchTaskResult(
                resource_id=unit_id,
                task_id=tid,
                status="interrupted",
                error=str(exc),
            )
        except Exception as exc:
            return BatchTaskResult(
                resource_id=unit_id,
                task_id=tid,
                status="failed",
                error=str(exc),
            )

    results = await asyncio.gather(*[_wait_one(s) for s in enqueued_specs])

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

    for br in enqueue_failures:
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
    batch_id: str | None = None,
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
            batch_id=batch_id,
        )
    )
