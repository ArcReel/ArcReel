"""Generation Worker restart recovery: reclaim orphan tasks, settle interrupted calls and dispatch bucketed resumptions.

Orphans arise only from process restarts (deployment and detection prerequisites in ``lib.generation.generation_worker`` and
``docs/adr/0007``). Routing must **never proactively incur extra charges**: if workers cannot confirm that accepted provider
jobs can resume, fail and discard their orphans, never resubmit. Download retries reuse the same resumption dispatch.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol

from lib.artifacts.rendered_artifact import cleanup_interrupted_render_staging
from lib.backends.providers import PROVIDER_GROK, PROVIDER_VIDU
from lib.config.service import read_video_poll_timeout_seconds
from lib.generation.render_lane import RENDER_MEDIA_TYPE
from lib.generation.task_failure import encode_failure
from lib.generation.video_resume import VideoResumeRunner, cleanup_video_staging
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import get_project_manager
from lib.script.reference_video.execution_checkpoint import VideoResumeState, classify_video_resume_state

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from lib.generation.generation_queue import GenerationQueue
    from lib.generation.generation_worker import CapacityTable, SlotTable

    ProviderProjection = Callable[[dict[str, Any]], Awaitable[str]]

logger = logging.getLogger(__name__)

# Video providers without VideoBackend.resume_video: orphan scans fail their running orphans as
# [resume_unsupported], never proactively requeue/rerun, avoiding double billing of already submitted provider requests
# (Grok is synchronous without job_id; Vidu polls inline in generate without independent resume).
# Register newly added nonresumable backends here too.
NON_RESUMABLE_VIDEO_PROVIDERS = frozenset({PROVIDER_GROK, PROVIDER_VIDU})


def _cleanup_orphan_render_staging(project_name: str) -> None:
    """Locate project directories and clean interrupted-render temporary files in worker threads."""

    cleanup_interrupted_render_staging(get_project_manager().get_project_path(project_name))


class InterruptedCallSettler(Protocol):
    """Startup settlement entry: return pending-call rows transitioned to terminal states. Settle taskless pending rows only when started before
    ``taskless_started_before``; ``None`` settles task-bound rows only."""

    def __call__(self, *, taskless_started_before: datetime | None) -> Awaitable[int]: ...


class RestartRecovery:
    """Restart-recovery collaborator held by ``GenerationWorker``.

    Share the worker's capacity table, slot ledger and stop signal (stable references, never rebuilt). Register resumption subtasks
    in the same slot ledger, drained by the worker main loop and awaited together on shutdown.
    """

    def __init__(
        self,
        *,
        queue: GenerationQueue,
        capacity: CapacityTable,
        slots: SlotTable,
        stop_event: asyncio.Event,
        reload_limits: Callable[[], Awaitable[None]],
        provider_projection: ProviderProjection,
        resume: VideoResumeRunner,
        settle_interrupted_calls: InterruptedCallSettler,
    ) -> None:
        self._queue = queue
        self._capacity = capacity
        self._slots = slots
        self._stop_event = stop_event
        self._reload_limits = reload_limits
        self._provider_projection = provider_projection
        self._resume = resume
        self._settle_interrupted_calls = settle_interrupted_calls
        # Persist orphan dispatcher handles for shutdown awaits; if lease takeover invokes handle_orphans
        # again, unfinished old handles cannot simply be overwritten.
        self.orphan_dispatcher_task: asyncio.Task | None = None
        # Download-retry dispatch is fire-and-forget. Without strong references, nothing outside the event loop retains tasks,
        # so GC may silently collect them at suspension points. Remove them from the set on completion.
        self._retry_dispatch_tasks: set[asyncio.Task] = set()

    async def wait_dispatchers(self) -> None:
        """On shutdown, wait for dispatchers to dispatch their last subtasks (which can otherwise be created after dispatcher exit).
        Dispatcher errors must not break the shutdown chain."""
        if self.orphan_dispatcher_task is not None and not self.orphan_dispatcher_task.done():
            try:
                await self.orphan_dispatcher_task
            except Exception:
                logger.exception("orphan dispatcher 在 shutdown 等待时异常")

        # The same applies to download-retry dispatchers: tasks become running before dispatch. If dispatchers exit before
        # registering subtasks, those tasks wait until the next startup orphan scan.
        if retry_dispatchers := [t for t in self._retry_dispatch_tasks if not t.done()]:
            await asyncio.gather(*retry_dispatchers, return_exceptions=True)

    async def settle_interrupted_calls(self, *, taskless_started_before: datetime | None) -> None:
        """After orphan handling, settle pending call rows without live tasks (sharing the orphan scan's one-shot switch).

        Order matters: orphan handling first makes unresumable tasks terminal, allowing settlement to use task outcomes;
        reversed, those tasks remain running and their calls are skipped as live. Log settlement failures without blocking
        claims: accounting cleanup is less important than worker startup.
        """
        try:
            settled = await self._settle_interrupted_calls(taskless_started_before=taskless_started_before)
        except Exception:
            logger.warning("启动收口 pending 调用行失败", exc_info=True)
            return
        if settled:
            logger.info(
                "启动收口：%d 条无存活任务的 pending 调用行已结算（%s）",
                settled,
                "仅绑定任务的行" if taskless_started_before is None else "含启动前发起的无任务行",
            )

    async def handle_orphans(self) -> None:
        """Restart recovery: scan running orphans and route by safe resumability.

        Principle: **never proactively incur extra charges**. Unless workers can confirm that accepted provider jobs can resume,
        fail and discard orphans, never resubmit.

        - image running → [restart_lost] (image tasks do not persist job_id and cannot resume;
          image submission is already billed, so rerunning doubles charges)
        - video running, provider ∈ NON_RESUMABLE_VIDEO_PROVIDERS (Grok/Vidu)
          → [resume_unsupported] (backends do not implement resume_video; original jobs cannot resume)
        - video running, resumable backend (ark/gemini/openai/newapi):
          - no provider_job_id → [restart_lost]
          - job_id present → collect into `resumable_by_provider` buckets; background dispatchers
            dispatch batches within video capacity

        The startup fast path (this function) **only performs terminal transitions** and returns immediately. Background dispatchers
        handle resumable video orphans, preventing N Sora orphans × 5 min polling each from blocking startup
        for tens of minutes. Dispatchers do not drain slot ledgers; they rely entirely on the worker main loop each cycle.
        Dispatchers naturally exit when the stop signal fires.
        """
        orphans = await self._queue.list_orphan_tasks_on_start()
        if not orphans:
            return
        logger.info("等待 lease 获取后开始扫孤儿（待处理 %d 个）", len(orphans))

        # Self-active prevents self-preemption: lease flaps beyond 3×TTL reset the one-shot scan switch;
        # rescanning DB running rows includes this process's still-inflight tasks. Without exclusion:
        # - image tasks → wrongly [restart_lost] while still running
        # - video tasks → duplicate resume flows polling/finalizing the same provider job concurrently;
        #   crash windows can double provider charges (violating ADR 0007's red line)
        # active_task_ids includes all pending+inflight occupancy; scanned DB tasks with those IDs are locally alive.
        self_active_task_ids = self._slots.active_task_ids()

        resumable_by_provider: dict[str, list[dict[str, Any]]] = {}

        for task in orphans:
            task_id = task["task_id"]
            if task_id in self_active_task_ids:
                logger.info("孤儿扫到本进程仍 active 的 task %s，跳过避免 self-preemption", task_id)
                continue
            task_type = task.get("task_type")
            if task.get("media_type"):
                media_type = task["media_type"]
            elif task_type in ("video", "reference_video"):
                media_type = "video"
            elif task_type == "tts":
                media_type = "audio"
            else:
                media_type = "image"

            # Image tasks persist no job_id and expose no resume entry; accepted provider requests cannot be recovered,
            # and proactive requeueing doubles charges. Discard directly; users decide whether to retry manually.
            if media_type == "image":
                logger.warning("孤儿 image running → [restart_lost]: %s", task_id)
                await self._queue.mark_task_failed(
                    task_id,
                    encode_failure("restart_lost_image"),
                )
                continue

            # Audio (TTS) is synchronous, persists no job_id and exposes no resume entry; degrade like image to
            # [restart_lost], without resubmission that could double bill.
            if media_type == "audio":
                logger.warning("孤儿 audio running → [restart_lost]: %s", task_id)
                await self._queue.mark_task_failed(
                    task_id,
                    encode_failure("restart_lost_audio"),
                )
                continue

            # Synchronous text calls may continue in threads, but no resumable job identity survives process restarts;
            # like image/audio, never resubmit automatically, avoiding duplicate charges.
            if media_type == "text":
                logger.warning("孤儿 text running → [restart_lost]: %s", task_id)
                await self._queue.mark_task_failed(task_id, encode_failure("restart_lost_text"))
                continue

            # Local renders do not resume; clean temporary files on restart, but retain this process's ongoing renders on lease takeover.
            # Never requeue automatically; users decide whether to render again.
            if media_type == RENDER_MEDIA_TYPE:
                logger.warning("孤儿 render running → [restart_lost]: %s", task_id)
                if not self._slots.occupied_providers(RENDER_MEDIA_TYPE):
                    try:
                        await run_sync_transaction(_cleanup_orphan_render_staging, task["project_name"])
                    except Exception:
                        logger.warning("清理孤儿 render 临时文件失败: %s", task_id, exc_info=True)
                await self._queue.mark_task_failed(task_id, encode_failure("restart_lost_render"))
                continue

            checkpoint = None
            if task_type in ("video", "reference_video"):
                resume_state, checkpoint = classify_video_resume_state(task)
                if resume_state is not VideoResumeState.READY:
                    if resume_state is VideoResumeState.NO_CHECKPOINT_NO_JOB:
                        failure = encode_failure("restart_lost_no_job_id")
                    elif resume_state is VideoResumeState.CHECKPOINT_WITHOUT_JOB:
                        failure = encode_failure("restart_lost_checkpoint_no_job_id")
                    else:
                        failure = encode_failure(
                            "execution_identity_unrecoverable",
                            detail="missing, malformed, or mismatched video submission checkpoint",
                        )
                    await self._queue.mark_task_failed(task_id, failure)
                    await cleanup_video_staging(task)
                    continue

            # Video path: determine provider resumability. Both generation modes use immutable checkpoints only;
            # otherwise, project configuration changes across restarts make provider projection resolve current state, potentially
            # misclassifying original Grok/Vidu orphans as resumable or routing resumable tasks to wrong pools.
            provider_id = (
                checkpoint.provider_id
                if checkpoint is not None
                else task.get("provider_id") or await self._provider_projection(task)
            )
            if provider_id in NON_RESUMABLE_VIDEO_PROVIDERS:
                # Grok/Vidu currently lack resume_video. Already submitted original jobs cannot resume,
                # and rerunning doubles charges. Discard; users manually decide whether to retry.
                logger.warning(
                    "孤儿 video running (provider=%s 不支持 resume) → [resume_unsupported]: %s",
                    provider_id,
                    task_id,
                )
                await self._queue.mark_task_failed(
                    task_id,
                    encode_failure("resume_unsupported_provider", provider_id=provider_id),
                )
                await cleanup_video_staging(task)
                continue

            job_id = task.get("provider_job_id")
            if not job_id:
                logger.warning("孤儿 running 无 job_id → [restart_lost]: %s", task_id)
                await self._queue.mark_task_failed(task_id, encode_failure("restart_lost_no_job_id"))
                await cleanup_video_staging(task)
                continue

            # Collect into provider buckets for background dispatchers to process batches within pool capacity.
            # Also write resolved provider_id into task dicts for dispatcher routing.
            task["provider_id"] = provider_id
            resumable_by_provider.setdefault(provider_id, []).append(task)

        if resumable_by_provider:
            total = sum(len(v) for v in resumable_by_provider.values())
            poll_timeout_seconds = await read_video_poll_timeout_seconds()
            for tasks in resumable_by_provider.values():
                for task in tasks:
                    task["video_poll_timeout_seconds"] = poll_timeout_seconds
            logger.info(
                "孤儿扫描 fast path 完成：%d 个可 resume video 任务交后台分批 dispatch",
                total,
            )
            # On lease takeover, old dispatchers may still run (typically provider polling inside resume_video
            # takes minutes to 10+ minutes). This pass overwrites handles **without awaiting or cancelling**:
            # - Never await: avoid blocking the main loop and harming liveness (lease heartbeats/cancel API responses).
            # - Never cancel: cancellation makes old dispatcher _run_one raise CancelledError, entering
            #   process-interruption fallback, wrongly marking still-resuming inflight tasks cancelled
            #   and leaving already billed provider jobs without owners.
            # - Overwrite directly: old dispatcher_task subtasks remain strongly referenced by the slot ledger (SlotTable)
            #   and asyncio.gather's internal callback chain; old tasks cannot become GC-detached.
            # - Shutdown still sees them: worker shutdown awaits old subtasks through _slots.all_active_tasks().
            if self.orphan_dispatcher_task is not None and not self.orphan_dispatcher_task.done():
                logger.warning(
                    "旧 orphan dispatcher 仍在运行，本轮直接覆盖句柄不等待——"
                    "旧 sub-task 由占用台账跟踪，shutdown 时经 _slots.all_active_tasks 兜底"
                )
            self.orphan_dispatcher_task = asyncio.create_task(
                self.dispatch_resumable(resumable_by_provider),
                name="orphan-dispatcher",
            )
        else:
            logger.info("孤儿扫描完成，无可 resume 任务")

    async def dispatch_resumable(
        self,
        resumable_by_provider: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Background dispatcher: concurrent provider buckets, batch inflight admission constrained by video capacity.

        - Different providers have independent capacities, so run independent subtasks concurrently;
        - Within a provider, enqueue sequentially; when full, `asyncio.wait(inflight, FIRST_COMPLETED)` waits for
          any completion (exact notification, no sleep polling);
        - The worker main loop drains done tasks each cycle, making slots available on the next dispatcher has_room
          check (the key decoupling assumption);
        - Naturally exit on the stop signal, holding no lease resources.
        """
        if self._stop_event.is_set():
            return
        sub_tasks = [
            asyncio.create_task(
                self.dispatch_provider_bucket(provider_id, tasks),
                name=f"orphan-dispatch-{provider_id}",
            )
            for provider_id, tasks in resumable_by_provider.items()
        ]
        await asyncio.gather(*sub_tasks, return_exceptions=True)
        logger.info("孤儿后台 dispatcher 完成")

    async def retry_artifact_download(self, task: dict[str, Any], *, poll_timeout_seconds: int) -> None:
        """Dispatch one download recovery against the original provider job, never resubmit provider tasks."""
        provider_id = task.get("provider_id")
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError("retry-download task has no provider_id")
        task["video_poll_timeout_seconds"] = poll_timeout_seconds
        dispatch = asyncio.create_task(
            self.dispatch_resumable({provider_id: [task]}),
            name=f"retry-download-{task['task_id']}",
        )
        self._retry_dispatch_tasks.add(dispatch)
        dispatch.add_done_callback(self._retry_dispatch_tasks.discard)

    async def dispatch_provider_bucket(
        self,
        provider_id: str,
        tasks: list[dict[str, Any]],
    ) -> None:
        """Run resume tasks concurrently within provider buckets; phase flags precisely track pending/inflight capacity and cancellation.

        - ``cap <= 0``: fail fast with mark_failed[resume_unsupported], never enter ``Semaphore(0)``
          deadlocks; reload once to avoid misclassification from startup capability fluctuations.
        - Parent coroutines synchronously preregister subtasks as PENDING in the slot ledger, preventing ``create_task``
          scheduling delays from letting main-loop ``has_room`` see zero occupancy and falsely available capacity.
        - Successful semaphore acquisition uses ``promote`` to turn occupancy INFLIGHT (same subtask, flag change only);
          finally ``release``.
        - Read semaphore capacity once from CapacityTable at dispatch entry. Capacity-table changes during reload
          do not affect this batch's concurrency limit. This is a known design choice, not a bug.
        """
        cap = self._capacity.get(provider_id, "video")
        if cap <= 0:
            # Startup reload fallback: DB loading may follow the first orphan scan.
            try:
                await self._reload_limits()
            except Exception:
                logger.warning("reload_limits 兜底失败", exc_info=True)
            cap = self._capacity.get(provider_id, "video")
        if cap <= 0:
            # Fail before dispatch, without exception objects: this sentence is the original failure text for these call rows, registered with backend
            # expiry/endpoint-change messages (agent-facing raw text, outside i18n, without classifiable machine codes).
            no_capacity = f"resume unsupported: provider {provider_id} has no video capacity"
            for t in tasks:
                await self._queue.mark_task_failed(
                    t["task_id"],
                    encode_failure("resume_unsupported_capacity_zero", provider_id=provider_id),
                )
                await self._resume.settle_unresumable_call(t, failure=no_capacity)
                await cleanup_video_staging(t)
            return

        sem = asyncio.Semaphore(cap)

        async def _run_one(t: dict[str, Any]) -> None:
            task_id = t["task_id"]
            acquired = False
            try:
                await sem.acquire()
                acquired = True
                if self._stop_event.is_set():
                    return
                # Stable slot objects (SlotTable references never rebuilt by reload); promote simply changes PENDING→INFLIGHT.
                self._slots.promote(provider_id, "video", task_id)
                logger.info("已派发 resume video orphan: task_id=%s provider=%s", task_id, provider_id)
                await self._resume.run(t)
            except asyncio.CancelledError:
                # Catch process-level interruptions at all three points here; SQL guards allow only queued / running, ensuring idempotence:
                # 1) While awaiting sem.acquire → resumption has not started; persist terminal state here.
                # 2) After acquired=True but before the inner resumption try → inner terminal paths do not run; persist terminal state here.
                # 3) Inside resumption → already made terminal internally; repeating here returns 0 rows for terminal rows, without side effects.
                try:
                    await asyncio.shield(self._queue.mark_task_interrupted(task_id))
                    await asyncio.shield(self._resume.settle_unresumable_call(t, cancelled=True))
                    await asyncio.shield(cleanup_video_staging(t))
                except Exception:
                    logger.exception("resume dispatch 打断后落终态失败 task_id=%s", task_id)
                raise
            finally:
                if acquired:
                    sem.release()
                self._slots.release(provider_id, "video", task_id)

        sub: list[asyncio.Task] = []
        for t in tasks:
            if self._stop_event.is_set():
                break
            # Parent coroutine synchronously creates tasks then immediately registers PENDING, avoiding the brief race where asynchronous
            # create_task scheduling has not run yet and has_room sees zero occupancy as available capacity.
            sub_task = asyncio.create_task(_run_one(t), name=f"resume-video-{t['task_id']}")
            self._slots.register(provider_id, "video", t["task_id"], sub_task, pending=True)
            sub.append(sub_task)
        if sub:
            await asyncio.gather(*sub, return_exceptions=True)
