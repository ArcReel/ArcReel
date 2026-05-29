"""
Background worker that consumes generation tasks from SQLite queue.

Per-provider pool scheduling: each provider gets independent concurrency
limits for image and video tasks, read from ConfigService (DB).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

from datetime import UTC

# Lease 丢失超过 ``lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT`` 才认为是真切换 owner
# （另一个 worker 进程曾持过 lease 且写入了新 orphan），需要重扫；短 flap（续约抖动）
# 不触发。lease_ttl 默认 10s → 阈值 30s。常量化便于单测注入与未来调参。
_ORPHAN_RESCAN_LEASE_LOST_MULT = 3

from lib.generation_queue import (
    TASK_POLL_INTERVAL_SEC,
    TASK_WORKER_HEARTBEAT_SEC,
    TASK_WORKER_LEASE_TTL_SEC,
    GenerationQueue,
    get_generation_queue,
)

# Default provider used when a task payload does not specify one.
DEFAULT_PROVIDER = "gemini-aistudio"


def _non_resumable_video_providers() -> frozenset[str]:
    """不实现 VideoBackend.resume_video 的视频 provider 集合。

    orphan handler 据此把这些 provider 的 running 孤儿标记为 [resume_unsupported]
    失败，而非主动 requeue 重跑——避免对已经提交给供应商的请求二次扣费
    （Grok 同步型无 job_id；Vidu 因 generate 内联 poll 未抽出独立 resume，列为
    follow-up）。新增不支持 resume 的 backend 时同步在这里登记。
    """
    from lib.providers import PROVIDER_GROK, PROVIDER_VIDU

    return frozenset({PROVIDER_GROK, PROVIDER_VIDU})


NON_RESUMABLE_VIDEO_PROVIDERS = _non_resumable_video_providers()


def _read_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


@dataclass
class ProviderPool:
    """Per-provider concurrency pool with independent image/video lanes.

    Video lane 还有一个 ``video_pending`` 字典：dispatcher 父协程 ``create_task`` 后
    sub-task 还在 sem 排队的瞬态。``has_video_room`` 计入 pending + inflight 严格
    限制总并发；``request_cancel`` 也查 pending，让排队中的孤儿可被秒级取消。
    Image lane 不引入 ``image_pending``——image 没有 sem-throttled dispatcher。
    """

    provider_id: str
    image_max: int  # 0 = this provider doesn't support image
    video_max: int  # 0 = this provider doesn't support video
    image_inflight: dict[str, asyncio.Task] = field(default_factory=dict)
    video_inflight: dict[str, asyncio.Task] = field(default_factory=dict)
    video_pending: dict[str, asyncio.Task] = field(default_factory=dict)

    def has_image_room(self) -> bool:
        return self.image_max > 0 and len(self.image_inflight) < self.image_max

    def has_video_room(self) -> bool:
        # 计入 pending：sem 排队期 sub-task 同样占名额，避免主循环超额 claim
        return self.video_max > 0 and len(self.video_inflight) + len(self.video_pending) < self.video_max

    def drain_finished(self) -> list[tuple[str, asyncio.Task]]:
        """Remove finished tasks from inflight dicts. Return ``(task_id, task)`` for inspection."""
        finished = []
        for inflight in (self.image_inflight, self.video_inflight):
            done_ids = [tid for tid, t in inflight.items() if t.done()]
            for tid in done_ids:
                finished.append((tid, inflight.pop(tid)))
        return finished

    def all_inflight(self) -> list[asyncio.Task]:
        """实际在跑的 task（不含 sem 排队中的 pending）。用于 metrics/真实并发统计。"""
        return [*self.image_inflight.values(), *self.video_inflight.values()]

    def all_active(self) -> list[asyncio.Task]:
        """In-flight + 排队中的 pending：用于 reload keep-alive 判定 / shutdown wait。"""
        return [*self.image_inflight.values(), *self.video_inflight.values(), *self.video_pending.values()]


async def _extract_provider(task: dict[str, Any]) -> str:
    """Extract a provider_id from a claimed task, used **only** for rate-limit pool routing.

    这是解析链的薄投影：按 media lane（``media_type``）派发到 ``resolve_video_backend`` /
    ``resolve_image_backend``，取 ``.provider_id``。image 任务一律按 ``capability="t2i"`` 取一个
    **代表性** provider——worker 认领时拿不到真实 capability（见 ``docs/adr/0001``），这点近似不影响
    生成正确性（执行层会独立精确再解析一次）。解析失败（未配置供应商）时回退到 DEFAULT_PROVIDER
    仅供限流，不阻断认领。
    """
    project_name = task.get("project_name")
    payload = task.get("payload") or {}
    # 以 media lane 区分 video / image：reference_video 等 task_type 同属 video lane。
    is_video = task.get("media_type") == "video" or task.get("task_type") in ("video", "reference_video")

    # 整体兜底：含项目加载（队列里可能有指向已删除/不可读项目的历史任务，load_project 会抛
    # FileNotFoundError）在内的任何失败都回退 DEFAULT_PROVIDER，绝不冒泡阻断认领循环（见 docstring）。
    try:
        project: dict | None = None
        if project_name:
            from lib.config.resolver import get_project_manager

            project = await asyncio.to_thread(get_project_manager().load_project, project_name)

        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        resolver = ConfigResolver(async_session_factory)
        if is_video:
            resolved = await resolver.resolve_video_backend(project, payload)
        else:
            resolved = await resolver.resolve_image_backend(project, payload, capability="t2i")
    except Exception:
        logger.debug("provider 解析失败，回退 DEFAULT_PROVIDER 仅供限流路由", exc_info=True)
        return DEFAULT_PROVIDER
    return resolved.provider_id or DEFAULT_PROVIDER


async def _load_pools_from_db() -> dict[str, ProviderPool]:
    """Load per-provider pool configs from ConfigService + PROVIDER_REGISTRY + custom providers."""
    from lib.config.registry import PROVIDER_REGISTRY
    from lib.config.service import ConfigService
    from lib.db import safe_session_factory
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    default_image = _read_int_env("IMAGE_MAX_WORKERS", 5, minimum=1)
    default_video = _read_int_env("VIDEO_MAX_WORKERS", 3, minimum=1)

    pools: dict[str, ProviderPool] = {}
    async with safe_session_factory() as session:
        svc = ConfigService(session)
        all_configs = await svc.get_all_provider_configs()
        for provider_id, meta in PROVIDER_REGISTRY.items():
            config = all_configs.get(provider_id, {})
            supports_image = "image" in meta.media_types
            supports_video = "video" in meta.media_types
            image_max = int(config.get("image_max_workers", str(default_image))) if supports_image else 0
            video_max = int(config.get("video_max_workers", str(default_video))) if supports_video else 0
            pools[provider_id] = ProviderPool(
                provider_id=provider_id,
                image_max=max(0, image_max),
                video_max=max(0, video_max),
            )

        # 加载自定义供应商的池配置（使用与内置供应商相同的默认值）
        from lib.custom_provider.endpoints import endpoint_to_media_type

        repo = CustomProviderRepository(session)
        for provider, models in await repo.list_providers_with_models():
            pid = provider.provider_id  # "custom-{id}"
            media_types = {endpoint_to_media_type(m.endpoint) for m in models if m.is_enabled}
            pools[pid] = ProviderPool(
                provider_id=pid,
                image_max=default_image if "image" in media_types else 0,
                video_max=default_video if "video" in media_types else 0,
            )

    logger.info(
        "从 DB 加载供应商池配置: %s",
        {pid: (p.image_max, p.video_max) for pid, p in pools.items()},
    )
    return pools


def _build_default_pools() -> dict[str, ProviderPool]:
    """Build pools from env vars / defaults (used before DB is available or in tests).

    为 PROVIDER_REGISTRY 中所有供应商创建默认池，避免 DB 加载前的任务
    因供应商未知而降级到 1 并发的 fallback 池。
    """
    from lib.config.registry import PROVIDER_REGISTRY

    image_max = _read_int_env("IMAGE_MAX_WORKERS", 5, minimum=1)
    video_max = _read_int_env("VIDEO_MAX_WORKERS", 3, minimum=1)

    pools: dict[str, ProviderPool] = {}
    for provider_id, meta in PROVIDER_REGISTRY.items():
        pools[provider_id] = ProviderPool(
            provider_id=provider_id,
            image_max=image_max if "image" in meta.media_types else 0,
            video_max=video_max if "video" in meta.media_types else 0,
        )
    return pools


class GenerationWorker:
    """Queue worker with per-provider image/video lanes and single-active lease."""

    def __init__(
        self,
        queue: GenerationQueue | None = None,
        lease_name: str = "default",
        pools: dict[str, ProviderPool] | None = None,
    ):
        self.queue = queue or get_generation_queue()
        self.lease_name = lease_name
        self.owner_id = f"worker-{uuid.uuid4().hex[:10]}"

        self._pools: dict[str, ProviderPool] = pools or _build_default_pools()
        logger.info(
            "Worker 初始池配置: %s",
            {pid: (p.image_max, p.video_max) for pid, p in self._pools.items()},
        )
        self.lease_ttl = max(1.0, float(TASK_WORKER_LEASE_TTL_SEC))
        self.heartbeat_interval = max(0.5, float(TASK_WORKER_HEARTBEAT_SEC))
        self.poll_interval = max(0.1, float(TASK_POLL_INTERVAL_SEC))

        self._main_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._owns_lease = False
        # Orphan dispatcher 句柄持久化：shutdown 时 await 它跑完；lease 切换重夺时
        # 第二次进 _handle_orphan_tasks_on_start，旧句柄未 done 不能直接覆盖。
        self._orphan_dispatcher_task: asyncio.Task | None = None
        # 一次性扫描开关：单 lease 互斥架构下，进程一旦扫过 orphan 就不再重扫；
        # 配合 _lease_lost_monotonic 阈值在「真切换 owner」时清零、「短 flap」不清零。
        self._orphan_handled_once: bool = False
        self._lease_lost_monotonic: float | None = None

    # ------------------------------------------------------------------
    # Backward compatibility shims
    # ------------------------------------------------------------------

    @property
    def image_workers(self) -> int:
        """Total image concurrency across all providers."""
        return sum(p.image_max for p in self._pools.values())

    @property
    def video_workers(self) -> int:
        """Total video concurrency across all providers."""
        return sum(p.video_max for p in self._pools.values())

    @property
    def _image_inflight(self) -> dict[str, asyncio.Task]:
        """Merged view of all image inflight tasks (read-only convenience)."""
        merged: dict[str, asyncio.Task] = {}
        for pool in self._pools.values():
            merged.update(pool.image_inflight)
        return merged

    @property
    def _video_inflight(self) -> dict[str, asyncio.Task]:
        """Merged view of all video inflight tasks (read-only convenience)."""
        merged: dict[str, asyncio.Task] = {}
        for pool in self._pools.values():
            merged.update(pool.video_inflight)
        return merged

    # ------------------------------------------------------------------
    # Pool management
    # ------------------------------------------------------------------

    def _get_or_create_pool(self, provider_id: str) -> ProviderPool:
        """Get pool for provider, creating a fallback pool if unknown."""
        pool = self._pools.get(provider_id)
        if pool is not None:
            return pool
        # Unknown provider — use same defaults as built-in providers
        image_max = _read_int_env("IMAGE_MAX_WORKERS", 5, minimum=1)
        video_max = _read_int_env("VIDEO_MAX_WORKERS", 3, minimum=1)
        pool = ProviderPool(
            provider_id=provider_id,
            image_max=image_max,
            video_max=video_max,
        )
        self._pools[provider_id] = pool
        logger.info("为供应商 %s 创建默认池 (image=%d, video=%d)", provider_id, image_max, video_max)
        return pool

    def _any_pool_has_room(self, media_type: str) -> bool:
        """Check if any provider pool has room for the given media_type."""
        for pool in self._pools.values():
            if media_type == "image" and pool.has_image_room():
                return True
            if media_type == "video" and pool.has_video_room():
                return True
        return False

    async def reload_limits(self) -> None:
        """Reload per-provider concurrency limits from DB.

        Preserves in-flight tasks: only updates max limits on existing pools
        and adds/removes pool entries as needed.
        """
        try:
            new_pools = await _load_pools_from_db()
        except Exception:
            logger.warning("从 DB 加载供应商配置失败，保持当前配置", exc_info=True)
            return

        # Migrate inflight + pending tasks to new pool objects；漏迁 video_pending
        # 会让 sem 排队中的 sub-task 引用旧 pool 字典，新 pool 看不见，cancel 失配
        # 且 keep-alive 判定漏掉这些 task。
        for pid, new_pool in new_pools.items():
            old_pool = self._pools.get(pid)
            if old_pool:
                new_pool.image_inflight = old_pool.image_inflight
                new_pool.video_inflight = old_pool.video_inflight
                new_pool.video_pending = old_pool.video_pending

        # Pools that existed before but are no longer registered:
        # 仍有 pending / inflight 的旧 pool 保留到耗尽——用 all_active 判定（含 pending）。
        for pid, old_pool in self._pools.items():
            if pid not in new_pools and old_pool.all_active():
                new_pools[pid] = old_pool
                new_pools[pid].image_max = 0
                new_pools[pid].video_max = 0

        self._pools = new_pools
        logger.info(
            "已更新供应商池配置: %s",
            {pid: (p.image_max, p.video_max) for pid, p in self._pools.items()},
        )

    def reload_limits_from_env(self) -> None:
        """Reload worker concurrency limits from environment variables.

        Backward-compatible shim. Prefer reload_limits() for DB-backed config.
        """
        image_max = _read_int_env("IMAGE_MAX_WORKERS", 3, minimum=1)
        video_max = _read_int_env("VIDEO_MAX_WORKERS", 2, minimum=1)
        default_pool = self._pools.get(DEFAULT_PROVIDER)
        if default_pool:
            default_pool.image_max = image_max
            default_pool.video_max = video_max
        else:
            self._pools[DEFAULT_PROVIDER] = ProviderPool(
                provider_id=DEFAULT_PROVIDER,
                image_max=image_max,
                video_max=video_max,
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._main_task and not self._main_task.done():
            return
        self._stop_event.clear()
        self._main_task = asyncio.create_task(self._run_loop(), name="generation-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._main_task:
            await self._main_task
            self._main_task = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                had_lease = self._owns_lease
                self._owns_lease = await self.queue.acquire_or_renew_worker_lease(
                    name=self.lease_name,
                    owner_id=self.owner_id,
                    ttl_seconds=self.lease_ttl,
                )

                if self._owns_lease and not had_lease:
                    logger.info("获得 worker lease (owner=%s)", self.owner_id)
                if had_lease and not self._owns_lease:
                    logger.warning("失去 worker lease (owner=%s)", self.owner_id)

                await self._drain_finished_tasks()

                # Lease 状态变化跟踪：首次失去 lease 时打点；重夺 lease 后判断
                # 「真切换 owner」（>= 3× ttl）→ 重置开关；「续约 flap」（< 3× ttl）→ 保持。
                if had_lease and not self._owns_lease and self._lease_lost_monotonic is None:
                    self._lease_lost_monotonic = time.monotonic()
                if self._owns_lease and self._lease_lost_monotonic is not None:
                    lost_duration = time.monotonic() - self._lease_lost_monotonic
                    if lost_duration > self.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT:
                        logger.info(
                            "lease 丢失 %.1fs（> %d×ttl=%.1fs），认为另一进程曾持过 lease，重扫 orphan",
                            lost_duration,
                            _ORPHAN_RESCAN_LEASE_LOST_MULT,
                            self.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT,
                        )
                        self._orphan_handled_once = False
                    self._lease_lost_monotonic = None

                # 一次性扫描：进程持 lease 后只扫一次 orphan；后续主循环不再重扫。
                # 单 lease 互斥保证不会与另一个 worker 同时扫；跨进程接管由上述阈值兜底。
                if self._owns_lease and not self._orphan_handled_once:
                    await self._handle_orphan_tasks_on_start()
                    self._orphan_handled_once = True

                if not self._owns_lease:
                    await asyncio.sleep(self.heartbeat_interval)
                    continue

                claimed_any = await self._claim_tasks()

                if claimed_any:
                    await asyncio.sleep(0.05)
                else:
                    await asyncio.sleep(self.poll_interval)

            await self._wait_inflight_completion()
        finally:
            if self._owns_lease:
                await self.queue.release_worker_lease(name=self.lease_name, owner_id=self.owner_id)
            self._owns_lease = False

    def _pool_full_providers(self, media_type: str) -> frozenset[str]:
        """返回当前 cycle ``media_type`` 池已满的 provider_id 集合（黑名单，用于 claim SQL）。

        黑名单语义而非白名单：白名单会把"DB 里有 provider_id 但不在已知 pool 集合"
        的任务（例如自定义 provider 已删除）永久过滤掉、静默堆积；黑名单只排除已知
        池满，未知 provider 任务正常 claim 走 worker 二次解析。

        注意守卫 ``*_max > 0``：``has_image_room()/has_video_room()`` 在 ``*_max == 0``
        时同样返回 ``False``，若不加守卫会把"不支持该 lane 的 provider"也归入黑名单，
        让 SQL 把这些 task 静默 drop，而不是走 worker 二次校验的 ``max_capacity == 0``
        fail-fast mark_failed 路径。
        """
        if media_type == "image":
            return frozenset(pid for pid, p in self._pools.items() if p.image_max > 0 and not p.has_image_room())
        return frozenset(pid for pid, p in self._pools.items() if p.video_max > 0 and not p.has_video_room())

    async def _claim_tasks(self) -> bool:
        """Claim tasks from queue and route to per-provider pools.

        池满 task 不再 claim → requeue 反复刷屏；改为在 SQL 层按
        ``pool_full_providers`` 黑名单过滤，池满 task 始终保持 ``queued``。
        ``provider_id IS NULL`` 老数据和未知 provider 任务不被过滤，claim 后由
        worker 二次 ``_extract_provider`` 派生 provider 再校验池容量。
        """
        claimed_any = False

        for media_type in ("image", "video"):
            while True:
                # 每轮重算池满集合：刚 claim 的任务可能让某 pool 进入满状态
                pool_full = self._pool_full_providers(media_type)
                task = await self.queue.claim_next_task(
                    media_type=media_type,
                    pool_full_providers=pool_full,
                )
                if not task:
                    break

                provider_id = await _extract_provider(task)
                pool = self._get_or_create_pool(provider_id)

                if media_type == "image":
                    max_capacity = pool.image_max
                    has_room = pool.has_image_room()
                else:
                    max_capacity = pool.video_max
                    has_room = pool.has_video_room()

                if max_capacity == 0:
                    # 供应商不支持此媒体类型（容量为 0），直接失败
                    logger.warning(
                        "供应商 %s 不支持 %s 生成，任务 %s 标记失败",
                        provider_id,
                        media_type,
                        task["task_id"],
                    )
                    await self.queue.mark_task_failed(
                        task["task_id"],
                        f"供应商 {provider_id} 不支持 {media_type} 生成",
                    )
                    claimed_any = True
                    continue

                if not has_room:
                    # NULL 老数据 / 未知 provider 通过 SQL 兜底走到这里：二次校验仍满
                    # → 回队让下次 cycle 再试（FIFO 顺序由 queued_at 维持）。绝不能
                    # mark_failed：入队后 provider_id 才被派生，资料完整的任务也可能
                    # 因部署窗口 / 解析失败而 NULL，这条路径必须保持可重试。
                    logger.info(
                        "供应商 %s 的 %s 池满，task %s 回队等待下一 cycle",
                        provider_id,
                        media_type,
                        task["task_id"],
                    )
                    await self._requeue_single_task(task["task_id"])
                    # break 当前 media_type 循环：下一轮 SQL 会按重算的 pool_full
                    # 过滤掉这个 provider，避免反复 claim 同一 task
                    break

                # Dispatch to pool
                claimed_any = True
                inflight = pool.image_inflight if media_type == "image" else pool.video_inflight
                inflight[task["task_id"]] = asyncio.create_task(
                    self._process_task(task),
                    name=f"generation-{media_type}-{task['task_id']}",
                )

        return claimed_any

    async def _requeue_single_task(self, task_id: str) -> None:
        """Put a claimed (running) task back to queued status.

        正常路径下大多数池满任务通过 ``pool_full_providers`` SQL 过滤在 claim 阶段被
        排除；当 ``provider_id IS NULL`` 走 IS NULL 兜底而 worker 二次校验发现池满时，
        本方法把任务放回 queued 等下次 cycle 重试（不可 mark_failed——派生 provider 在
        入队后才发生，NULL 不等于"无效任务"）。
        """
        try:
            from datetime import datetime

            from sqlalchemy import update

            from lib.db import safe_session_factory
            from lib.db.models.task import Task

            async with safe_session_factory() as session:
                await session.execute(
                    update(Task)
                    .where(Task.task_id == task_id, Task.status == "running")
                    .values(
                        status="queued",
                        started_at=None,
                        updated_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            logger.debug("回队任务 %s (供应商池已满)", task_id)
        except Exception:
            logger.warning("回队任务 %s 失败", task_id, exc_info=True)

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    async def _drain_finished_tasks(self) -> None:
        for pool in self._pools.values():
            for task_id, finished_task in pool.drain_finished():
                # 同步判定取消/异常：drain_finished() 只返回 done() 的 task，无需 await。
                # 不 await 就没有挂起点，自然不会误吞针对 _run_loop 自身的取消信号。
                if finished_task.cancelled():
                    # 子任务被取消。正常路径 _process_task 已 mark_cancelled 并 re-raise；
                    # 但取消可能落在 _process_task 进入 try 之前（协程尚未开始执行，或仍停在
                    # 入口的 _extract_provider await），那一刻子任务来不及落终态。drain 端兜底
                    # mark_cancelled——SQL 守卫 status IN (queued, cancelling, running) 保证幂等：
                    # 已落终态则 0 rows 无副作用，避免任务永久卡在 running/cancelling。
                    try:
                        await self.queue.mark_task_cancelled(task_id, cancelled_by="user")
                    except Exception:
                        logger.warning("drain 兜底 mark_cancelled 失败 task_id=%s", task_id, exc_info=True)
                    continue
                try:
                    finished_task.result()
                except Exception:
                    logger.debug("已处理的任务异常已在 _process_task 中记录")

    async def _wait_inflight_completion(self) -> None:
        # shutdown：先等 dispatcher 派完最后一批 sub-task（否则 sub-task 可能在 dispatcher
        # 退出后才创建），再等所有 active task。dispatcher 异常不能断 shutdown 链。
        if self._orphan_dispatcher_task is not None and not self._orphan_dispatcher_task.done():
            try:
                await self._orphan_dispatcher_task
            except Exception:
                logger.exception("orphan dispatcher 在 shutdown 等待时异常")

        active_tasks = []
        for pool in self._pools.values():
            active_tasks.extend(pool.all_active())
        if not active_tasks:
            return
        await asyncio.gather(*active_tasks, return_exceptions=True)
        for pool in self._pools.values():
            pool.image_inflight.clear()
            pool.video_inflight.clear()
            pool.video_pending.clear()

    async def _process_task(self, task: dict[str, Any]) -> None:
        """Run a generation task with 0-rows-cancelled finally protocol (ADR 0006).

        所有 DB 写入（mark_succeeded / mark_failed / mark_cancelled）都用 ``asyncio.shield``
        包裹：若取消信号在 DB 写入 await 期间到达，inner shield 让 UPDATE 跑完再向外
        传播，避免任务停在 cancelling/running 中间态。
        """
        task_id = task["task_id"]
        task_type = task.get("task_type", "unknown")
        provider_id = await _extract_provider(task)
        logger.info("开始处理任务 %s (type=%s, provider=%s)", task_id, task_type, provider_id)

        from server.services.generation_tasks import execute_generation_task

        try:
            result = await execute_generation_task(task)
        except asyncio.CancelledError:
            # 用户/级联取消：worker.request_cancel 触发 asyncio.Task.cancel()
            await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            raise
        except Exception as exc:
            logger.exception("任务失败 %s (type=%s, provider=%s)", task_id, task_type, provider_id)
            rows = await asyncio.shield(self.queue.mark_task_failed(task_id, str(exc)))
            if rows == 0:
                # 外部已抢先翻 cancelling → 落地 cancelled 终态
                await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            return

        try:
            rows = await asyncio.shield(self.queue.mark_task_succeeded(task_id, result))
        except asyncio.CancelledError:
            # mark_succeeded 期间被取消：shield 让 inner 跑完了；inner 完成情况由
            # rowcount 决定——拿不到了，按"被外部取消"语义兜底。
            await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            raise
        except Exception:
            # mark_succeeded 自身抛错（DB 超时 / OperationalError）：上层 _drain_finished_tasks
            # 只吞掉异常 debug 日志，stack trace 会丢失，因此在这里显式 logger.exception 保留现场。
            logger.exception("标记任务成功失败 %s", task_id)
            raise
        if rows == 0:
            # 0-rows-cancelled 协议：execute 跑赢但 DB 已被外部翻 cancelling
            await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
        else:
            logger.info("任务完成 %s (type=%s, provider=%s)", task_id, task_type, provider_id)

    async def _process_resume_task(self, task: dict[str, Any]) -> None:
        """重启自愈入口：直接调 backend.resume_video，绕过 normal executor 流水线。

        provider 锁定：把持久化的 ``task["provider_id"]`` 注入 payload 的
        ``video_provider`` 字段，让 ``ConfigResolver`` 按持久化 provider 而非当前
        项目配置解析 backend。否则任务提交后到重启前若项目 provider 配置切换，
        会拿旧 ``provider_job_id`` 去新 provider 轮询，导致可恢复任务被误判失败。
        """
        task_id = task["task_id"]
        task_type = task.get("task_type", "unknown")

        job_id = task.get("provider_job_id") or ""
        if not job_id:
            # 防御：本不该被派发到这里（_handle_orphan_tasks_on_start 已 mark_failed [restart_lost]）
            rows = await asyncio.shield(
                self.queue.mark_task_failed(task_id, "[restart_lost] 无 provider_job_id 但被派发到 resume")
            )
            if rows == 0:
                await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            return

        # 锁定持久化 provider 到 payload（resolver 优先级：payload > project > 默认）。
        persisted_provider_id = task.get("provider_id")
        if persisted_provider_id:
            payload = task.get("payload")
            if payload is None:
                payload = {}
                task["payload"] = payload
            is_video = task.get("media_type") == "video" or task_type in ("video", "reference_video")
            if is_video:
                payload["video_provider"] = persisted_provider_id
            else:
                payload["image_provider"] = persisted_provider_id

        provider_id = await _extract_provider(task)
        logger.info(
            "重启自愈处理任务 %s (type=%s, provider=%s, job=%s)",
            task_id,
            task_type,
            provider_id,
            job_id,
        )

        from lib.video_backends.base import ResumeExpiredError
        from server.services.resume_executor import execute_resume_video_task

        try:
            result = await execute_resume_video_task(task, job_id=job_id)
        except asyncio.CancelledError:
            await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            raise
        except NotImplementedError as exc:
            logger.warning("resume 不支持 task %s: %s", task_id, exc)
            rows = await asyncio.shield(self.queue.mark_task_failed(task_id, f"[resume_unsupported] {exc}"))
            if rows == 0:
                await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            return
        except ResumeExpiredError as exc:
            logger.warning("resume 已过期 task %s: %s", task_id, exc)
            rows = await asyncio.shield(self.queue.mark_task_failed(task_id, f"[resume_expired] {exc}"))
            if rows == 0:
                await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            return
        except Exception as exc:
            logger.exception("resume 失败 %s (type=%s, provider=%s)", task_id, task_type, provider_id)
            rows = await asyncio.shield(self.queue.mark_task_failed(task_id, str(exc)))
            if rows == 0:
                await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            return

        try:
            rows = await asyncio.shield(self.queue.mark_task_succeeded(task_id, result))
        except asyncio.CancelledError:
            await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
            raise
        if rows == 0:
            await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
        else:
            logger.info("重启自愈完成 %s", task_id)

    # ------------------------------------------------------------------
    # Cancel & orphan recovery
    # ------------------------------------------------------------------

    def request_cancel(self, task_id: str) -> bool:
        """In-process cancel 信号：把 task 对应 asyncio.Task cancel()，返回是否找到。

        由 GenerationQueue.cancel_task 同步调用（ADR 0006 秒级响应）。也查 video_pending：
        sem 排队中的 sub-task cancel 会让 sem.acquire 抛 CancelledError 让 sub-task
        直接退出。callback 不命中是 best-effort 失败——worker finally 走 mark_cancelled
        兜底（SQL 守卫 IN queued|cancelling|running 接住）。
        """
        for pool in self._pools.values():
            for inflight in (pool.image_inflight, pool.video_inflight, pool.video_pending):
                t = inflight.get(task_id)
                if t is not None and not t.done():
                    t.cancel()
                    logger.info("已对 task %s 发出 in-process cancel 信号", task_id)
                    return True
        logger.info("request_cancel: task %s 不在 inflight (worker finally 兜底)", task_id)
        return False

    async def _handle_orphan_tasks_on_start(self) -> None:
        """重启自愈：扫 running + cancelling 孤儿，按"是否可安全 resume"分流。

        原则——**不主动产生额外扣费**：只要 worker 不能确认能接续供应商已收单的 job，
        就把孤儿标记为失败丢弃，绝不重新提交。

        - cancelling → mark_cancelled
        - image running → [restart_lost]（image 任务不持久化 job_id，无法接续；
          且 image 提交本身已计费，重跑等于双重扣费）
        - video running，provider ∈ NON_RESUMABLE_VIDEO_PROVIDERS（Grok/Vidu）
          → [resume_unsupported]（backend 不实现 resume_video，原 job 无接续手段）
        - video running，可 resume backend (ark/gemini/openai/newapi)：
          - 无 provider_job_id → [restart_lost]
          - 有 job_id → 收集到 `resumable_by_provider` 桶，后台 dispatcher 受
            pool video_max 容量约束分批 dispatch（fix #647 第 1 项）

        启动期 fast path（本函数）**只做终结类处理**，立刻返回；可 resume 的视频孤儿
        派发给后台 dispatcher 处理，避免 N 个 Sora orphan × 每个 5min poll 把启动期
        阻塞数十分钟。Dispatcher 不调 `_drain_finished_tasks`，完全依赖主循环每 cycle
        清理 inflight 字典；`_stop_event` 触发时 dispatcher 自然退出。
        """
        orphans = await self.queue.list_orphan_tasks_on_start()
        if not orphans:
            return
        logger.info(
            "等待 lease 获取后开始扫孤儿（待处理 %d 个）…lease_ttl=%.0fs",
            len(orphans),
            self.lease_ttl,
        )

        # self-active 防 self-preemption：lease flap > 3×TTL 后 _orphan_handled_once
        # 重置，再扫 DB running 会包含本进程仍 inflight 的 task。若不排除：
        # - image 任务 → 错误标 [restart_lost]（任务还在跑就被标失败）
        # - video 任务 → 启动重复 resume 流，同一 provider job 被并发 poll/finalize，
        #   崩溃窗口可能导致 provider 端双重扣费（违反 ADR 0007 红线）
        # 收集本进程当前在跑的 task_id（image_inflight + video_inflight + video_pending），
        # DB 扫到的同 id task 视为本进程的活，跳过孤儿处理。
        self_active_task_ids: set[str] = set()
        for pool in self._pools.values():
            self_active_task_ids.update(pool.image_inflight.keys())
            self_active_task_ids.update(pool.video_inflight.keys())
            self_active_task_ids.update(pool.video_pending.keys())

        resumable_by_provider: dict[str, list[dict[str, Any]]] = {}

        for task in orphans:
            task_id = task["task_id"]
            if task_id in self_active_task_ids:
                logger.info("孤儿扫到本进程仍 active 的 task %s，跳过避免 self-preemption", task_id)
                continue
            status = task.get("status")
            if status == "cancelling":
                await self.queue.mark_task_cancelled(task_id, cancelled_by="user")
                logger.info("孤儿 cancelling → cancelled: %s", task_id)
                continue

            # status == "running"
            media_type = task.get("media_type") or (
                "video" if task.get("task_type") in ("video", "reference_video") else "image"
            )

            # image 任务不持久化 job_id 也无 resume 入口——已提交给供应商的请求无法回收，
            # 主动 requeue 会双重扣费。直接丢弃，等用户决定是否手动重试。
            if media_type == "image":
                logger.warning("孤儿 image running → [restart_lost]: %s", task_id)
                rows = await self.queue.mark_task_failed(
                    task_id,
                    "[restart_lost] image 任务无法接续，需手动重试以避免重复计费",
                )
                if rows == 0:
                    await self.queue.mark_task_cancelled(task_id, cancelled_by="user")
                continue

            # video 路径：判断 provider 是否支持 resume。优先用持久化的 provider_id：
            # 否则项目配置在重启前后切换时，_extract_provider 会按当前项目重新解析，
            # 可能把原本 Grok/Vidu 孤儿误判成可 resume，或把可 resume 任务路由到错池。
            # 与 _process_resume_task 的 provider 锁定策略保持一致。
            provider_id = task.get("provider_id") or await _extract_provider(task)
            if provider_id in NON_RESUMABLE_VIDEO_PROVIDERS:
                # Grok/Vidu 当前不实现 resume_video——原 job 已发给供应商无接续手段，
                # 重跑会重复扣费。丢弃，由用户手动决定是否重试。
                logger.warning(
                    "孤儿 video running (provider=%s 不支持 resume) → [resume_unsupported]: %s",
                    provider_id,
                    task_id,
                )
                rows = await self.queue.mark_task_failed(
                    task_id,
                    f"[resume_unsupported] provider={provider_id} 不支持接续，需手动重试以避免重复计费",
                )
                if rows == 0:
                    await self.queue.mark_task_cancelled(task_id, cancelled_by="user")
                continue

            job_id = task.get("provider_job_id")
            if not job_id:
                logger.warning("孤儿 running 无 job_id → [restart_lost]: %s", task_id)
                rows = await self.queue.mark_task_failed(
                    task_id, "[restart_lost] worker 重启时未持久化 provider_job_id"
                )
                if rows == 0:
                    await self.queue.mark_task_cancelled(task_id, cancelled_by="user")
                continue

            # 收集到 provider 桶，交给后台 dispatcher 受 pool 容量约束分批处理。
            # 顺便把 resolve 出的 provider_id 写回 task dict，dispatcher 路由用。
            task["provider_id"] = provider_id
            resumable_by_provider.setdefault(provider_id, []).append(task)

        if resumable_by_provider:
            total = sum(len(v) for v in resumable_by_provider.values())
            logger.info(
                "孤儿扫描 fast path 完成：%d 个可 resume video 任务交后台分批 dispatch",
                total,
            )
            # lease 重夺时旧 dispatcher 可能还在跑（典型场景：resume_video 内 poll provider
            # 需要几分钟到 10+ 分钟）。本轮**不 await 不 cancel** 直接覆盖句柄：
            # - 不 await：避免阻塞主循环 → liveness 问题（无法续 lease 心跳/无法响应 cancel API）
            # - 不 cancel：cancel 会让旧 dispatcher 的 _run_one 抛 CancelledError，进入
            #   兜底 mark_task_cancelled 路径，把用户**未主动取消**的 in-flight resume 错误
            #   标为 cancelled，且让 provider 端已扣费 job 失去归属
            # - 直接覆盖：旧 dispatcher_task 的 sub-task 仍由 pool.video_pending/video_inflight
            #   持有引用 + asyncio.gather 内部 callback 链持有，旧 task 不会被 GC detached
            # - shutdown 仍能感知：_wait_inflight_completion 经 pool.all_active() 等到旧 sub-task
            if self._orphan_dispatcher_task is not None and not self._orphan_dispatcher_task.done():
                logger.warning(
                    "旧 orphan dispatcher 仍在运行，本轮直接覆盖句柄不等待——"
                    "旧 sub-task 由 pool 跟踪，shutdown 时经 pool.all_active 兜底"
                )
            self._orphan_dispatcher_task = asyncio.create_task(
                self._dispatch_resume_orphans_background(resumable_by_provider),
                name="orphan-dispatcher",
            )
        else:
            logger.info("孤儿扫描完成，无可 resume 任务")

    async def _dispatch_resume_orphans_background(
        self,
        resumable_by_provider: dict[str, list[dict[str, Any]]],
    ) -> None:
        """后台 dispatcher：按 provider 分桶并发，受 pool video_max 容量约束分批入 inflight。

        - 不同 provider 之间无容量耦合 → 并发跑独立 sub-task；
        - 同 provider 内顺序入队：满则 `asyncio.wait(inflight, FIRST_COMPLETED)` 等任一
          完成（精确感知，不 sleep 轮询）；
        - 主循环每 cycle 调 `_drain_finished_tasks` 自动 pop 已 done 的 task → dispatcher
          下次 has_room 判定就有空位（解耦关键假设）；
        - `_stop_event` 触发时 dispatcher 自然退出，不持有 lease 资源。
        """
        if self._stop_event.is_set():
            return
        sub_tasks = [
            asyncio.create_task(
                self._dispatch_provider_bucket(provider_id, tasks),
                name=f"orphan-dispatch-{provider_id}",
            )
            for provider_id, tasks in resumable_by_provider.items()
        ]
        await asyncio.gather(*sub_tasks, return_exceptions=True)
        logger.info("孤儿后台 dispatcher 完成")

    async def _dispatch_provider_bucket(
        self,
        provider_id: str,
        tasks: list[dict[str, Any]],
    ) -> None:
        """同 provider 桶并发跑 resume task，pending/inflight 分集合精确容量与 cancel 跟踪。

        - ``pool.video_max <= 0``：fail-fast mark_failed[resume_unsupported]，不进
          ``Semaphore(0)`` 死锁；reload 一次兜底，避免启动期 capability 抖动误判。
        - sub-task 由父协程同步预先注册到 ``pool.video_pending``——避免 ``create_task``
          异步调度还未触发时主循环 ``has_video_room`` 看 inflight={} 误判可有容量。
        - sem acquire 成功后从 pending 移到 inflight；finally 两个 dict 都 pop。
        - sem 闭包旧 pool 限制：本 sem = Semaphore(pool.video_max) 在 dispatch 顶部
          捕获 pool 时定型；reload 期间替换的新 pool 不会影响 sem。这是本批 dispatch
          内并发上限维持 reload 前值的已知设计选择，非 bug。
        """
        pool = self._get_or_create_pool(provider_id)
        if pool.video_max <= 0:
            # 启动期 reload 兜底：DB 加载可能晚于第一次 orphan 扫描。
            try:
                await self.reload_limits()
            except Exception:
                logger.warning("reload_limits 兜底失败", exc_info=True)
            pool = self._get_or_create_pool(provider_id)
        if pool.video_max <= 0:
            for t in tasks:
                rows = await self.queue.mark_task_failed(
                    t["task_id"],
                    f"[resume_unsupported] provider {provider_id} video_max=0",
                )
                if rows == 0:
                    await self.queue.mark_task_cancelled(t["task_id"], cancelled_by="user")
            return

        sem = asyncio.Semaphore(pool.video_max)

        async def _run_one(t: dict[str, Any]) -> None:
            task_id = t["task_id"]
            acquired = False
            try:
                await sem.acquire()
                acquired = True
                if self._stop_event.is_set():
                    return
                # acquire 后 pool 可能在 reload_limits 期间被换新引用；
                # 务必从 self._pools 重读，保证 inflight 字典写入新 pool
                pool_now = self._get_or_create_pool(provider_id)
                pool_now.video_pending.pop(task_id, None)
                current = asyncio.current_task()
                if current is not None:
                    pool_now.video_inflight[task_id] = current
                logger.info("已派发 resume video orphan: task_id=%s provider=%s", task_id, provider_id)
                await self._process_resume_task(t)
            except asyncio.CancelledError:
                # 三种 cancel 路径都在这里兜底 mark_task_cancelled——SQL WHERE
                # status IN (queued, cancelling, running) 保证幂等：
                # 1) sem.acquire 等待期 cancel → _process_resume_task 还没跑，必须由此落终态
                # 2) acquired=True 后但 _process_resume_task 内 try 块外（如 _extract_provider
                #    的 await）cancel → 内部 mark 路径不会触发，必须由此落终态（CR round 2 #6）
                # 3) _process_resume_task 内部 cancel → 内部已 mark，此处再调 SQL 命中
                #    cancelled 行返回 0 rows，无副作用
                try:
                    await asyncio.shield(self.queue.mark_task_cancelled(task_id, cancelled_by="user"))
                except Exception:
                    logger.exception("sem dispatch cancel 落终态失败 task_id=%s", task_id)
                raise
            finally:
                if acquired:
                    sem.release()
                # 同样重读以应对 reload race
                pool_now = self._get_or_create_pool(provider_id)
                pool_now.video_pending.pop(task_id, None)
                pool_now.video_inflight.pop(task_id, None)

        sub: list[asyncio.Task] = []
        for t in tasks:
            if self._stop_event.is_set():
                break
            # 父协程同步：先 create_task、再立即写入 pending dict——避免「create_task
            # 是异步调度，has_video_room 在调度未发生前看 inflight={} 误判可有容量」
            # 的瞬时 race。
            sub_task = asyncio.create_task(_run_one(t), name=f"resume-video-{t['task_id']}")
            pool.video_pending[t["task_id"]] = sub_task
            sub.append(sub_task)
        if sub:
            await asyncio.gather(*sub, return_exceptions=True)
