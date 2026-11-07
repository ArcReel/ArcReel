"""Resume persisted provider video jobs without resubmission.

``lib.generation.restart_recovery`` dispatches interrupted videos after restart,
or user retry-download tasks. Application assembly injects resume executors.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol

from arcreel_market_core.video_backend_contract import ResumeEndpointChangedError, ResumeExpiredError
from lib.billing.ledger import Ledger
from lib.db.repositories.project_records import is_deleted_project_name
from lib.generation.media_generator import cleanup_staged_video_output
from lib.generation.task_failure import encode_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import get_project_manager
from lib.project.task_project_claim import TaskProjectClaim, claim_task_project
from lib.script.reference_video.execution_checkpoint import (
    VideoResumeState,
    classify_video_resume_state,
    cleanup_staged_provider_media,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lib.generation.generation_queue import GenerationQueue

    ProviderProjection = Callable[[dict[str, Any]], Awaitable[str]]

logger = logging.getLogger(__name__)


class ResumeExecutor(Protocol):
    """Resume ``job_id``, persist artifacts, return task results."""

    def __call__(self, task: dict[str, Any], *, job_id: str) -> Awaitable[dict[str, Any]]: ...


async def cleanup_video_staging(task: dict[str, Any]) -> None:
    """Best-effort cleanup when either video route becomes terminal outside normal finalization."""

    if task.get("task_type") not in ("video", "reference_video"):
        return
    try:
        project_path = await asyncio.to_thread(get_project_manager().get_project_path, task["project_name"])
    except Exception:
        logger.warning("video staging project lookup failed task_id=%s", task.get("task_id"), exc_info=True)
        return

    resource_id = task.get("resource_id")
    if resource_id is not None:
        try:
            resource_type = "reference_videos" if task["task_type"] == "reference_video" else "videos"
            await asyncio.to_thread(
                cleanup_staged_video_output,
                project_path,
                resource_type,
                str(resource_id),
                task["task_id"],
            )
        except Exception:
            logger.warning("video formal output cleanup failed task_id=%s", task.get("task_id"), exc_info=True)

    try:
        await asyncio.to_thread(cleanup_staged_provider_media, project_path, task["task_id"])
    except Exception:
        logger.warning("video provider media cleanup failed task_id=%s", task.get("task_id"), exc_info=True)


async def fail_task_of_deleted_project(queue: GenerationQueue, task: dict[str, Any]) -> None:
    """Fail deleted-project tasks as ``project_deleted_during_task``, removing empty directories recreated under old names."""

    task_id = task["task_id"]
    logger.warning("任务 %s 执行期间项目 %s 已删除，放弃落盘", task_id, task.get("project_name"))
    await asyncio.shield(queue.mark_task_failed(task_id, encode_failure("project_deleted_during_task")))
    try:
        # Getting project managers touches filesystems through initial directory creation; thread the whole operation. Deletion writes formal files.
        await run_sync_transaction(lambda: get_project_manager().remove_project_directory_residue(task["project_name"]))
    except Exception:
        logger.warning("已删除项目的残余目录清理失败 task_id=%s", task_id, exc_info=True)


class VideoResumeRunner:
    """Run resume tasks to terminals, settling associated pending calls."""

    def __init__(
        self,
        *,
        queue: GenerationQueue,
        resume_executor: ResumeExecutor,
        provider_projection: ProviderProjection,
    ) -> None:
        self._queue = queue
        self._resume_executor = resume_executor
        self._provider_projection = provider_projection

    async def run(self, task: dict[str, Any]) -> None:
        """Restart-recovery entry directly calls resume executors, bypassing normal pipelines.

        Both video modes materialize latest states at worker start and persist immutable
        execution checkpoints before provider submission. After restart, construct fixed resolved requests from checkpoints only,
        never recompute identities from current projects/enqueue payloads.

        Nonvideo fallback injects persisted ``task["provider_id"]`` into payload ``image_provider``,
        locking providers, not models. Orphan scans dispatch videos only; images/audio already
        receive ``[restart_lost]``, so only dirty media_type reaches this branch.

        Resume retains normal project claims. Deletion during resume fails
        ``project_deleted_during_task``; prerestart deletions leave tombstone project names/unresolvable directories,
        producing the same code.
        """
        project_name = task.get("project_name")
        if isinstance(project_name, str) and is_deleted_project_name(project_name):
            logger.warning("任务 %s 的项目已删除，不再续跑", task["task_id"])
            await asyncio.shield(
                self._queue.mark_task_failed(task["task_id"], encode_failure("project_deleted_during_task"))
            )
            await asyncio.shield(self.settle_unresumable_call(task, failure="project deleted before resume"))
            return
        with claim_task_project(task["task_id"], project_name) as claim:
            await self._run_claimed(task, claim)

    async def _run_claimed(self, task: dict[str, Any], claim: TaskProjectClaim) -> None:
        task_id = task["task_id"]
        task_type = task.get("task_type", "unknown")

        checkpoint = None
        if task_type in ("video", "reference_video"):
            state, checkpoint = classify_video_resume_state(task)
            if state is not VideoResumeState.READY:
                code = (
                    "restart_lost_checkpoint_no_job_id"
                    if state is VideoResumeState.CHECKPOINT_WITHOUT_JOB
                    else "restart_lost_no_job_id"
                    if state is VideoResumeState.NO_CHECKPOINT_NO_JOB
                    else "execution_identity_unrecoverable"
                )
                params = (
                    {"detail": "missing, malformed, or mismatched video submission checkpoint"}
                    if (code == "execution_identity_unrecoverable")
                    else {}
                )
                await asyncio.shield(self._queue.mark_task_failed(task_id, encode_failure(code, **params)))
                await cleanup_video_staging(task)
                return

        job_id = task.get("provider_job_id") or ""
        if not job_id:
            # Defensive: orphan scans already mark_failed [restart_lost], so dispatch here is unexpected.
            await asyncio.shield(self._queue.mark_task_failed(task_id, encode_failure("restart_lost_resume_no_job_id")))
            await cleanup_video_staging(task)
            return

        # Nonvideos freeze persisted providers into payload, resolver priority payload>project>defaults.
        # Submitted video identities use checkpoints, never injection which freezes providers only, not models.
        persisted_provider_id = checkpoint.provider_id if checkpoint is not None else task.get("provider_id")
        is_video = task.get("media_type") == "video" or task_type in ("video", "reference_video")
        if persisted_provider_id and not is_video:
            payload = task.get("payload")
            if payload is None:
                payload = {}
                task["payload"] = payload
            payload["image_provider"] = persisted_provider_id

        provider_id = checkpoint.provider_id if checkpoint is not None else await self._provider_projection(task)
        logger.info(
            "重启自愈处理任务 %s (type=%s, provider=%s, job=%s)",
            task_id,
            task_type,
            provider_id,
            job_id,
        )

        async def _execute_with_video_cleanup() -> dict[str, Any]:
            try:
                return await self._resume_executor(task, job_id=job_id)
            finally:
                if checkpoint is not None:
                    await asyncio.shield(cleanup_video_staging(task))

        # Resume has no new accounting scope; all terminal exits settle original pending
        # ApiCall rows. Retry-download reopens pending, making every exit below reachable.
        # WHERE status=pending guards make repeat resume settlement harmless.
        try:
            result = await _execute_with_video_cleanup()
        except asyncio.CancelledError:
            # Process interruption: finalize tasks and settle pending calls cancelled.
            await asyncio.shield(self._queue.mark_task_interrupted(task_id))
            await asyncio.shield(self.settle_unresumable_call(task, cancelled=True))
            raise
        except NotImplementedError as exc:
            logger.warning("resume 不支持 task %s: %s", task_id, exc)
            await asyncio.shield(
                self._queue.mark_task_failed(task_id, encode_failure("resume_unsupported_detail", detail=str(exc)))
            )
            await asyncio.shield(self.settle_unresumable_call(task, failure=exc))
            return
        except ResumeEndpointChangedError as exc:
            logger.warning("resume endpoint 已变更 task %s: %s", task_id, exc)
            await asyncio.shield(
                self._queue.mark_task_failed(task_id, encode_failure("resume_endpoint_changed_detail", detail=str(exc)))
            )
            await asyncio.shield(self.settle_unresumable_call(task, failure=exc))
            return
        except ResumeExpiredError as exc:
            logger.warning("resume 已过期 task %s: %s", task_id, exc)
            await asyncio.shield(
                self._queue.mark_task_failed(task_id, encode_failure("resume_expired_detail", detail=str(exc)))
            )
            await asyncio.shield(self.settle_unresumable_call(task, failure=exc))
            return
        except Exception as exc:
            if claim.revoked:
                await fail_task_of_deleted_project(self._queue, task)
            else:
                logger.exception("resume 失败 %s (type=%s, provider=%s)", task_id, task_type, provider_id)
                await asyncio.shield(self._queue.mark_task_failed(task_id, encode_task_failure_message(exc)))
            await asyncio.shield(self.settle_unresumable_call(task, failure=exc))
            return

        if claim.revoked:
            await fail_task_of_deleted_project(self._queue, task)
            return
        try:
            await asyncio.shield(self._queue.mark_task_succeeded(task_id, result))
        except asyncio.CancelledError:
            await asyncio.shield(self._queue.mark_task_interrupted(task_id))
            raise
        logger.info("重启自愈完成 %s", task_id)

    def _ledger(self) -> Ledger:
        """Resume accounting shares queue session factories, never separate global engines.

        Task/call rows must share DBs. Injected test/independent-schema factories paired with global accounting
        would finalize wrong rows and hold cross-event-loop connections.
        """
        return Ledger(session_factory=self._queue.session_factory)

    async def settle_unresumable_call(
        self, task: dict[str, Any], *, cancelled: bool = False, failure: BaseException | str | None = None
    ) -> None:
        """Finalize unresumable pending ApiCall as task failed/cancelled, zero cost.

        Resume opens no new accounting scopes because submission already accounted. Dispatch terminal failures must settle
        calls too, or permanently pending usage remains, especially retry-download reopening calls.

        ``failure`` is caller except-branch exceptions that made resume terminal: task encoding belongs to tasks,
        while call raw text/codes persist here only. Predispatch failures without objects pass raw text.
        Cancellation omits failures because canceled rows have no failure reasons.
        """
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return

        call_id: int | None = None
        try:
            ledger = self._ledger()
            call_id = await ledger.pending_call_id_for_task(task_id)
            if call_id is None:
                return
            if cancelled:
                await ledger.resume_cancelled(call_id=call_id)
            else:
                await ledger.resume_failed(call_id=call_id, failure=failure)
        except Exception:
            logger.warning("pending ApiCall 结算失败 task_id=%s call_id=%s", task_id, call_id, exc_info=True)
