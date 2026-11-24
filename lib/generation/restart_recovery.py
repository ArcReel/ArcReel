"""生成 Worker 的重启自愈：回收孤儿任务、结算被打断的调用、分桶派发续跑。

孤儿只来自进程重启（部署与判定前提见 ``lib.generation.generation_worker`` 与
``docs/adr/0007``）。分流原则是**不主动产生额外扣费**：worker 不能确认能接续供应商已收单的
job 时，把孤儿标记为失败丢弃，绝不重新提交。「重试下载」复用同一条续跑派发。
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol

from lib.artifacts.rendered_artifact import cleanup_interrupted_render_staging
from lib.artifacts.version_manager import VersionManager
from lib.backends.providers import PROVIDER_GROK, PROVIDER_VIDU
from lib.config.service import read_video_poll_timeout_seconds
from lib.db.repositories.project_records import is_deleted_project_name
from lib.generation.render_lane import RENDER_MEDIA_TYPE
from lib.generation.task_failure import encode_failure
from lib.generation.video_resume import VideoResumeRunner, cleanup_video_staging
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import get_project_manager
from lib.project.resource_paths import resource_relative_path
from lib.script.reference_video.execution_checkpoint import VideoResumeState, classify_video_resume_state

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from lib.generation.generation_queue import GenerationQueue
    from lib.generation.generation_worker import CapacityTable, SlotTable
    from lib.generation.task_ownership import TaskLease, TaskOwnership
    from lib.generation.task_writes import TaskWrites

    ProviderProjection = Callable[[dict[str, Any]], Awaitable[str]]

logger = logging.getLogger(__name__)

NON_RESUMABLE_VIDEO_PROVIDERS = frozenset({PROVIDER_GROK, PROVIDER_VIDU})


def _cleanup_orphan_render_staging(project_name: str) -> None:
    """在工作线程里定位项目目录并清理中断渲染留下的临时文件。"""

    cleanup_interrupted_render_staging(get_project_manager().get_project_path(project_name))


def _committed_video_result(task: dict[str, Any]) -> dict[str, Any] | None:
    if task.get("task_type") not in ("video", "reference_video") or task.get("resource_id") is None:
        return None
    if is_deleted_project_name(task["project_name"]):
        return None
    try:
        path = get_project_manager().get_project_path(task["project_name"])
    except FileNotFoundError:
        return None
    resource_type = "reference_videos" if task["task_type"] == "reference_video" else "videos"
    resource_id = str(task["resource_id"])
    history = VersionManager(path).get_versions(resource_type, resource_id)
    for record in reversed(history["versions"]):
        if record.get("task_id", record.get("execution_task_id")) == task["task_id"]:
            result = {
                "version": record["version"],
                "file_path": record["file"],
                "resource_type": resource_type,
                "resource_id": resource_id,
                "video_uri": record.get("video_uri"),
                "created_at": record["created_at"],
            }
            if record["is_current"]:
                result["file_path"] = resource_relative_path(resource_type, resource_id)
            else:
                result["selected_current"] = False
            if resource_type == "reference_videos" or record.get("warnings"):
                result["warnings"] = record.get("warnings", [])
            return result
    return None


class InterruptedCallSettler(Protocol):
    """启动收口入口：返回已翻成终态的 pending 调用行数。无任务身份的 pending 行只收口在
    ``taskless_started_before`` 之前发起的，传 ``None`` 则只收口绑定了任务的行。"""

    def __call__(self, *, taskless_started_before: datetime | None) -> Awaitable[int]: ...


class RestartRecovery:
    """重启自愈协作者，由 ``GenerationWorker`` 持有。

    与 worker 共用同一张容量表、占用台账与停止信号（引用恒定，不被重建）：续跑 sub-task 登记
    进同一份占用台账，由 worker 主循环 drain、关停时一并等待。
    """

    def __init__(
        self,
        *,
        queue: GenerationQueue,
        ownership: TaskOwnership,
        writes: TaskWrites,
        request_rescan: Callable[[], None],
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
        self._ownership = ownership
        self._writes = writes
        self._request_rescan = request_rescan
        self._dispatchers: set[asyncio.Task] = set()

    def _track(self, dispatcher: asyncio.Task) -> None:
        self._dispatchers.add(dispatcher)
        dispatcher.add_done_callback(self._dispatchers.discard)

    async def wait_dispatchers(self) -> None:
        while self._dispatchers:
            await asyncio.gather(*self._dispatchers, return_exceptions=True)

    async def _still_running(self, task_id: str) -> bool:
        try:
            row = await self._queue.get_task(task_id)
        except Exception:
            logger.warning("续跑前读取任务失败 task_id=%s", task_id, exc_info=True)
            self._request_rescan()
            return False
        return row is not None and row["status"] == "running"

    async def _restore_committed(self, task: dict[str, Any]) -> bool:
        if task.get("task_type") not in ("video", "reference_video"):
            return False
        result = await run_sync_transaction(_committed_video_result, task)
        if result is None:
            return False
        await self._writes.succeeded(task["task_id"], result)
        await cleanup_video_staging(task)
        return True

    async def settle_interrupted_calls(self, *, taskless_started_before: datetime | None) -> None:
        """孤儿任务处理之后收口没有存活任务的 pending 调用行（与孤儿扫描共用一次性开关）。

        顺序不能反：孤儿处理先把无法接续的任务翻成终态，收口才能按任务的结局判定它的调用行；
        反过来那些任务还是 running，它们的调用行会被当成「任务还活着」跳过。收口失败只记日志，
        不阻断认领循环——账目收尾比不上 worker 起不来。
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
        """重启自愈：扫 running 孤儿，按"是否可安全 resume"分流。

        原则——**不主动产生额外扣费**：只要 worker 不能确认能接续供应商已收单的 job，
        就把孤儿标记为失败丢弃，绝不重新提交。

        - image running → [restart_lost]（image 任务不持久化 job_id，无法接续；
          且 image 提交本身已计费，重跑等于双重扣费）
        - video running，provider ∈ NON_RESUMABLE_VIDEO_PROVIDERS（Grok/Vidu）
          → [resume_unsupported]（backend 不实现 resume_video，原 job 无接续手段）
        - video running，可 resume backend (ark/gemini/openai/newapi)：
          - 无 provider_job_id → [restart_lost]
          - 有 job_id → 收集到 `resumable_by_provider` 桶，后台 dispatcher 受
            video 容量约束分批 dispatch

        启动期 fast path（本函数）**只做终结类处理**，立刻返回；可 resume 的视频孤儿
        派发给后台 dispatcher 处理，避免 N 个 Sora orphan × 每个 5min poll 把启动期
        阻塞数十分钟。Dispatcher 不 drain 占用台账，完全依赖 worker 主循环每 cycle
        清理；停止信号触发时 dispatcher 自然退出。
        """
        orphans = await self._queue.list_orphan_tasks_on_start()
        if not orphans:
            return
        logger.info("等待 lease 获取后开始扫孤儿（待处理 %d 个）", len(orphans))

        resumable_by_provider: dict[str, list[dict[str, Any]]] = {}
        for task in orphans:
            task_id = task["task_id"]
            lease = self._ownership.try_acquire(task_id)
            if lease is None:
                continue
            try:
                if await self._still_running(task_id) and not await self._restore_committed(task):
                    provider = await self._classify_orphan(task)
                    if provider is not None:
                        task["provider_id"] = provider
                        resumable_by_provider.setdefault(provider, []).append(task)
            finally:
                lease.release()

        if resumable_by_provider:
            timeout = await read_video_poll_timeout_seconds()
            for tasks in resumable_by_provider.values():
                for task in tasks:
                    task["video_poll_timeout_seconds"] = timeout
            self._track(asyncio.create_task(self.dispatch_resumable(resumable_by_provider), name="orphan-dispatcher"))

    async def _classify_orphan(self, task: dict[str, Any]) -> str | None:
        task_id = task["task_id"]
        task_type = task.get("task_type")
        if task.get("media_type"):
            media_type = task["media_type"]
        elif task_type in ("video", "reference_video"):
            media_type = "video"
        elif task_type == "tts":
            media_type = "audio"
        else:
            media_type = "image"

        if media_type == "image":
            logger.warning("孤儿 image running → [restart_lost]: %s", task_id)
            await self._writes.failed(
                task_id,
                encode_failure("restart_lost_image"),
            )
            return None

        if media_type == "audio":
            logger.warning("孤儿 audio running → [restart_lost]: %s", task_id)
            await self._writes.failed(
                task_id,
                encode_failure("restart_lost_audio"),
            )
            return None

        if media_type == "text":
            logger.warning("孤儿 text running → [restart_lost]: %s", task_id)
            await self._writes.failed(task_id, encode_failure("restart_lost_text"))
            return None

        if media_type == RENDER_MEDIA_TYPE:
            logger.warning("孤儿 render running → [restart_lost]: %s", task_id)
            if not self._slots.occupied_providers(RENDER_MEDIA_TYPE):
                try:
                    await run_sync_transaction(_cleanup_orphan_render_staging, task["project_name"])
                except Exception:
                    logger.warning("清理孤儿 render 临时文件失败: %s", task_id, exc_info=True)
            await self._writes.failed(task_id, encode_failure("restart_lost_render"))
            return None

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
                await self._writes.failed(task_id, failure)
                await cleanup_video_staging(task)
                return None

        provider_id = (
            checkpoint.provider_id
            if checkpoint is not None
            else task.get("provider_id") or await self._provider_projection(task)
        )
        if provider_id in NON_RESUMABLE_VIDEO_PROVIDERS:
            logger.warning(
                "孤儿 video running (provider=%s 不支持 resume) → [resume_unsupported]: %s",
                provider_id,
                task_id,
            )
            await self._writes.failed(
                task_id,
                encode_failure("resume_unsupported_provider", provider_id=provider_id),
            )
            await cleanup_video_staging(task)
            return None

        job_id = task.get("provider_job_id")
        if not job_id:
            logger.warning("孤儿 running 无 job_id → [restart_lost]: %s", task_id)
            await self._writes.failed(task_id, encode_failure("restart_lost_no_job_id"))
            await cleanup_video_staging(task)
            return None

        return provider_id

    async def dispatch_resumable(
        self,
        resumable_by_provider: dict[str, list[dict[str, Any]]],
    ) -> None:
        """后台 dispatcher：按 provider 分桶并发，受 video 容量约束分批入 inflight。

        - 不同 provider 之间无容量耦合 → 并发跑独立 sub-task；
        - 同 provider 内顺序入队：满则 `asyncio.wait(inflight, FIRST_COMPLETED)` 等任一
          完成（精确感知，不 sleep 轮询）；
        - worker 主循环每 cycle drain 已 done 的 task → dispatcher 下次 has_room 判定就有
          空位（解耦关键假设）；
        - 停止信号触发时 dispatcher 自然退出，不持有 lease 资源。
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

    def retry_artifact_download(self, task: dict[str, Any], *, poll_timeout_seconds: int) -> None:
        task["video_poll_timeout_seconds"] = poll_timeout_seconds
        self._track(
            asyncio.create_task(
                self.dispatch_provider_bucket(task["provider_id"], [task], accepted_retry=True),
                name=f"retry-download-{task['task_id']}",
            )
        )

    async def dispatch_provider_bucket(
        self,
        provider_id: str,
        tasks: list[dict[str, Any]],
        *,
        accepted_retry: bool = False,
    ) -> None:
        """同 provider 桶并发跑 resume task，pending/inflight 用 phase 标志精确容量与 cancel 跟踪。

        - ``cap <= 0``：fail-fast mark_failed[resume_unsupported]，不进 ``Semaphore(0)``
          死锁；reload 一次兜底，避免启动期 capability 抖动误判。
        - sub-task 由父协程同步预先以 PENDING 登记到占用台账——避免 ``create_task``
          异步调度还未触发时主循环 ``has_room`` 看占用=0 误判可有容量。
        - sem acquire 成功后 ``promote`` 把该占用翻成 INFLIGHT（同一 sub-task，只翻标志）；
          finally ``release``。
        - sem 容量在 dispatch 顶部从 CapacityTable 读一次定型：reload 期间改的容量表
          不影响本批 dispatch 的并发上限。这是已知设计选择，非 bug。
        """
        cap = self._capacity.get(provider_id, "video")
        if cap <= 0:
            try:
                await self._reload_limits()
            except Exception:
                logger.warning("reload_limits 兜底失败", exc_info=True)
            cap = self._capacity.get(provider_id, "video")
        sem = asyncio.Semaphore(max(cap, 1))

        async def _run_one(t: dict[str, Any]) -> None:
            task_id = t["task_id"]
            acquired = False
            try:
                await sem.acquire()
                acquired = True
                if (self._stop_event.is_set() and not accepted_retry) or not await self._still_running(task_id):
                    return
                if await self._restore_committed(t):
                    return
                if cap <= 0:
                    await self._writes.failed(
                        task_id, encode_failure("resume_unsupported_capacity_zero", provider_id=provider_id)
                    )
                    await self._resume.settle_unresumable_call(
                        t, failure=f"resume unsupported: provider {provider_id} has no video capacity"
                    )
                    await cleanup_video_staging(t)
                    return
                self._slots.promote(provider_id, "video", task_id)
                logger.info("已派发 resume video orphan: task_id=%s provider=%s", task_id, provider_id)
                await self._resume.run(t)
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(self._queue.mark_task_interrupted(task_id))
                    await asyncio.shield(self._resume.settle_unresumable_call(t, cancelled=True))
                    await asyncio.shield(cleanup_video_staging(t))
                except Exception:
                    logger.exception("resume dispatch 打断后落终态失败 task_id=%s", task_id)
                raise
            except Exception:
                logger.exception("视频任务收尾失败 task_id=%s", task_id)
                self._request_rescan()
            finally:
                if acquired:
                    sem.release()

        def release(lease: TaskLease, _done: asyncio.Task) -> None:
            self._slots.release(provider_id, "video", lease.task_id)
            lease.release()

        sub: list[asyncio.Task] = []
        for t in tasks:
            if self._stop_event.is_set() and not accepted_retry:
                break
            lease = (
                await self._ownership.acquire(t["task_id"])
                if accepted_retry
                else self._ownership.try_acquire(t["task_id"])
            )
            if lease is None:
                continue
            sub_task = asyncio.create_task(_run_one(t), name=f"resume-video-{t['task_id']}")
            self._slots.register(provider_id, "video", t["task_id"], sub_task, pending=True)
            sub_task.add_done_callback(partial(release, lease))
            sub.append(sub_task)
        if sub:
            await asyncio.gather(*sub, return_exceptions=True)
