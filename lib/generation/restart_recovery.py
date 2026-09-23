"""生成 Worker 的重启自愈：回收孤儿任务、结算被打断的调用、分桶派发续跑。

孤儿只来自进程重启（部署与判定前提见 ``lib.generation.generation_worker`` 与
``docs/adr/0007``）。分流原则是**不主动产生额外扣费**：worker 不能确认能接续供应商已收单的
job 时，把孤儿标记为失败丢弃，绝不重新提交。「重试下载」复用同一条续跑派发。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol

from lib.backends.providers import PROVIDER_GROK, PROVIDER_VIDU
from lib.config.service import read_video_poll_timeout_seconds
from lib.generation.task_failure import encode_failure
from lib.generation.video_resume import VideoResumeRunner, cleanup_video_staging
from lib.script.reference_video.execution_checkpoint import VideoResumeState, classify_video_resume_state

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from lib.generation.generation_queue import GenerationQueue
    from lib.generation.generation_worker import CapacityTable, SlotTable

    ProviderProjection = Callable[[dict[str, Any]], Awaitable[str]]

logger = logging.getLogger(__name__)

# 不实现 VideoBackend.resume_video 的视频 provider：孤儿扫描把它们的 running 孤儿标记为
# [resume_unsupported] 失败，而非主动 requeue 重跑——避免对已经提交给供应商的请求二次扣费
# （Grok 同步型无 job_id；Vidu 的 generate 内联 poll，没有独立 resume）。
# 新增不支持 resume 的 backend 时同步在这里登记。
NON_RESUMABLE_VIDEO_PROVIDERS = frozenset({PROVIDER_GROK, PROVIDER_VIDU})


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
        # Orphan dispatcher 句柄持久化：shutdown 时 await 它跑完；lease 切换重夺时
        # 第二次进 handle_orphans，旧句柄未 done 不能直接覆盖。
        self.orphan_dispatcher_task: asyncio.Task | None = None
        # 「重试下载」的派发是即发即忘的：不持强引用的话，事件循环之外无人引用该 task，
        # 它可能在挂起点被 GC 静默回收。完成即从集合摘除。
        self._retry_dispatch_tasks: set[asyncio.Task] = set()

    async def wait_dispatchers(self) -> None:
        """shutdown：等 dispatcher 派完最后一批 sub-task（否则 sub-task 可能在 dispatcher 退出后
        才创建）。dispatcher 异常不能断 shutdown 链。"""
        if self.orphan_dispatcher_task is not None and not self.orphan_dispatcher_task.done():
            try:
                await self.orphan_dispatcher_task
            except Exception:
                logger.exception("orphan dispatcher 在 shutdown 等待时异常")

        # 「重试下载」的 dispatcher 同理：任务在派发之前就已经被翻成 running，dispatcher 还没
        # 登记 sub-task 就退出的话，这一笔要等到下次启动的孤儿扫描才被接手。
        if retry_dispatchers := [t for t in self._retry_dispatch_tasks if not t.done()]:
            await asyncio.gather(*retry_dispatchers, return_exceptions=True)

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

        # self-active 防 self-preemption：lease flap > 3×TTL 后一次性扫描开关
        # 重置，再扫 DB running 会包含本进程仍 inflight 的 task。若不排除：
        # - image 任务 → 错误标 [restart_lost]（任务还在跑就被标失败）
        # - video 任务 → 启动重复 resume 流，同一 provider job 被并发 poll/finalize，
        #   崩溃窗口可能导致 provider 端双重扣费（违反 ADR 0007 红线）
        # active_task_ids 含 pending+inflight 全部占用，DB 扫到的同 id task 视为本进程的活。
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

            # image 任务不持久化 job_id 也无 resume 入口——已提交给供应商的请求无法回收，
            # 主动 requeue 会双重扣费。直接丢弃，等用户决定是否手动重试。
            if media_type == "image":
                logger.warning("孤儿 image running → [restart_lost]: %s", task_id)
                await self._queue.mark_task_failed(
                    task_id,
                    encode_failure("restart_lost_image"),
                )
                continue

            # audio（TTS）同步、不持久化 job_id、无 resume 入口——与 image 同样降级为
            # [restart_lost]，不重新提交以免重复计费。
            if media_type == "audio":
                logger.warning("孤儿 audio running → [restart_lost]: %s", task_id)
                await self._queue.mark_task_failed(
                    task_id,
                    encode_failure("restart_lost_audio"),
                )
                continue

            # text 同步调用可能在线程中继续运行，但进程重启后没有可接续的 job identity；
            # 与 image/audio 一样不自动重交，避免重复计费。
            if media_type == "text":
                logger.warning("孤儿 text running → [restart_lost]: %s", task_id)
                await self._queue.mark_task_failed(task_id, encode_failure("restart_lost_text"))
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

            # video 路径：判断 provider 是否支持 resume。两种生成模式均只用不可变 checkpoint；
            # 否则项目配置在重启前后切换时，provider 投影会按当前项目重新解析，可能把原本
            # Grok/Vidu 孤儿误判成可 resume，或把可 resume 任务路由到错池。
            provider_id = (
                checkpoint.provider_id
                if checkpoint is not None
                else task.get("provider_id") or await self._provider_projection(task)
            )
            if provider_id in NON_RESUMABLE_VIDEO_PROVIDERS:
                # Grok/Vidu 当前不实现 resume_video——原 job 已发给供应商无接续手段，
                # 重跑会重复扣费。丢弃，由用户手动决定是否重试。
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

            # 收集到 provider 桶，交给后台 dispatcher 受 pool 容量约束分批处理。
            # 顺便把 resolve 出的 provider_id 写回 task dict，dispatcher 路由用。
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
            # lease 重夺时旧 dispatcher 可能还在跑（典型场景：resume_video 内 poll provider
            # 需要几分钟到 10+ 分钟）。本轮**不 await 不 cancel** 直接覆盖句柄：
            # - 不 await：避免阻塞主循环 → liveness 问题（无法续 lease 心跳/无法响应 cancel API）
            # - 不 cancel：cancel 会让旧 dispatcher 的 _run_one 抛 CancelledError，进入
            #   进程级打断的兜底路径，把仍在接续的 in-flight resume 错误标为 cancelled，
            #   且让 provider 端已扣费 job 失去归属
            # - 直接覆盖：旧 dispatcher_task 的 sub-task 仍由占用台账（SlotTable）持有引用
            #   + asyncio.gather 内部 callback 链持有，旧 task 不会被 GC detached
            # - shutdown 仍能感知：worker 关停经 _slots.all_active_tasks() 等到旧 sub-task
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

    async def retry_artifact_download(self, task: dict[str, Any], *, poll_timeout_seconds: int) -> None:
        """按原 provider job 派发一次下载恢复，不重新提交供应商任务。"""
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
            # 启动期 reload 兜底：DB 加载可能晚于第一次 orphan 扫描。
            try:
                await self._reload_limits()
            except Exception:
                logger.warning("reload_limits 兜底失败", exc_info=True)
            cap = self._capacity.get(provider_id, "video")
        if cap <= 0:
            # 派发前就判死，手上没有异常对象：这一句就是这些调用行的失败原文，与 backend 抛出的
            # 过期/换端点消息同一登记册（agent-facing 原文，不进 i18n，也没有可分类的机器码）。
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
                # 占用对象恒定（SlotTable 引用不被 reload 重建），promote 直接翻 PENDING→INFLIGHT
                self._slots.promote(provider_id, "video", task_id)
                logger.info("已派发 resume video orphan: task_id=%s provider=%s", task_id, provider_id)
                await self._resume.run(t)
            except asyncio.CancelledError:
                # 进程级打断落在三处都在这里兜底——SQL 守卫只放行 queued / running，保证幂等：
                # 1) sem.acquire 等待期 → 续跑还没开始，必须由此落终态
                # 2) acquired=True 后但续跑内 try 块外 → 内部落终态路径不会触发，必须由此落终态
                # 3) 续跑内部 → 内部已落终态，此处再调命中终态行返回 0 rows，无副作用
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
            # 父协程同步：先 create_task、再立即以 PENDING 登记——避免「create_task
            # 是异步调度，has_room 在调度未发生前看占用=0 误判可有容量」的瞬时 race。
            sub_task = asyncio.create_task(_run_one(t), name=f"resume-video-{t['task_id']}")
            self._slots.register(provider_id, "video", t["task_id"], sub_task, pending=True)
            sub.append(sub_task)
        if sub:
            await asyncio.gather(*sub, return_exceptions=True)
