"""视频任务续跑：按已持久化的 provider job 接续，不重新提交供应商任务。

续跑由重启自愈（``lib.generation.restart_recovery``）派发：进程重启后接续被打断的视频任务，
或用户对产物下载失败的任务发起「重试下载」。续跑执行器由应用装配处注入。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol

from lib.backends.video_backend_contract import ResumeEndpointChangedError, ResumeExpiredError
from lib.billing.ledger import Ledger
from lib.generation.media_generator import cleanup_staged_video_output
from lib.generation.task_failure import encode_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.project.project_manager import get_project_manager
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
    """续跑执行器：按 ``job_id`` 接续供应商任务并完成产物落盘，返回任务结果。"""

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


class VideoResumeRunner:
    """把一个续跑任务跑到终态，并结算它那条 pending 的调用行。"""

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
        """重启自愈入口：直接调续跑执行器，绕过常规执行流水线。

        两种视频生成模式都在 worker 开始时按最新状态物化，并在 provider 提交前写入不可变
        execution checkpoint。重启后只从 checkpoint 构造固定的解析请求，不从当前项目
        配置或 enqueue payload 重算执行身份。

        非视频媒体退到把持久化的 ``task["provider_id"]`` 注入 payload 的 ``image_provider``
        字段（只锁 provider、锁不住 model）。孤儿扫描只把 video 交到这里，image / audio 孤儿
        在扫描期即落 ``[restart_lost]``，该分支因而只在 media_type 为脏数据时可达。
        """
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
            # 防御：本不该被派发到这里（孤儿扫描已 mark_failed [restart_lost]）
            await asyncio.shield(self._queue.mark_task_failed(task_id, encode_failure("restart_lost_resume_no_job_id")))
            await cleanup_video_staging(task)
            return

        # 非视频媒体：锁定持久化 provider 到 payload（resolver 优先级：payload > project > 默认）。
        # 已提交视频任务的身份走 checkpoint，不在此注入——注入只覆盖 provider、盖不住 model。
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

        # 续跑不开新的记账括号（账是提交时记的），任何终态出口都要顺手结算那条 pending 的
        # ApiCall，否则用量报表里留下永不终态的行。「重试下载」把调用重开成 pending 之后，
        # 下面每一条出口都变得可达。resume 结算带 WHERE status='pending'，重复调用无副作用。
        try:
            result = await _execute_with_video_cleanup()
        except asyncio.CancelledError:
            # 进程级打断：落终态并把 pending 调用行结算为 cancelled。
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
            logger.exception("resume 失败 %s (type=%s, provider=%s)", task_id, task_type, provider_id)
            await asyncio.shield(self._queue.mark_task_failed(task_id, encode_task_failure_message(exc)))
            await asyncio.shield(self.settle_unresumable_call(task, failure=exc))
            return

        try:
            await asyncio.shield(self._queue.mark_task_succeeded(task_id, result))
        except asyncio.CancelledError:
            await asyncio.shield(self._queue.mark_task_interrupted(task_id))
            raise
        logger.info("重启自愈完成 %s", task_id)

    def _ledger(self) -> Ledger:
        """续跑侧的记账入口：与队列共用同一处 session factory，不另接全局引擎。

        任务行与它的调用行必须落在同一个库里——队列注入了别的 session factory（测试库、
        独立 schema）时，记账若仍走全局引擎，会翻错库里的行，还会跨事件循环持有连接。
        """
        return Ledger(session_factory=self._queue.session_factory)

    async def settle_unresumable_call(
        self, task: dict[str, Any], *, cancelled: bool = False, failure: BaseException | str | None = None
    ) -> None:
        """把无法继续的 pending ApiCall 按任务终态结算为 failed / cancelled（零费用）。

        续跑路径不开新的记账括号——账是提交时记的。派发侧终态失败若只翻任务不结算调用，
        那条 pending 会永久留在用量报表里；重试下载尤其明显：它刚把调用重开成 pending。

        ``failure`` 是把这次续跑判死的那个异常，由调用方从自己的 except 分支传下来：任务侧
        的失败编码是任务的，调用行的失败原文与机器码只能从这里落。派发前判死时没有异常对象，
        改传一段失败原文。取消出口不传——已取消的行没有失败原因可写。
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
