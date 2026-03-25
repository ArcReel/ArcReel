"""
Background worker that consumes generation tasks from SQLite queue.

Per-provider pool scheduling: each provider gets independent concurrency
limits for image and video tasks, read from ConfigService (DB).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict

logger = logging.getLogger(__name__)

from lib.generation_queue import (
    GenerationQueue,
    TASK_POLL_INTERVAL_SEC,
    TASK_WORKER_HEARTBEAT_SEC,
    TASK_WORKER_LEASE_TTL_SEC,
    get_generation_queue,
)

# Default provider used when a task payload does not specify one.
DEFAULT_PROVIDER = "gemini-aistudio"


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
    """Per-provider concurrency pool with independent image/video lanes."""

    provider_id: str
    image_max: int  # 0 = this provider doesn't support image
    video_max: int  # 0 = this provider doesn't support video
    image_inflight: dict[str, asyncio.Task] = field(default_factory=dict)
    video_inflight: dict[str, asyncio.Task] = field(default_factory=dict)

    def has_image_room(self) -> bool:
        return self.image_max > 0 and len(self.image_inflight) < self.image_max

    def has_video_room(self) -> bool:
        return self.video_max > 0 and len(self.video_inflight) < self.video_max

    def drain_finished(self) -> list[asyncio.Task]:
        """Remove finished tasks from inflight dicts. Return them for await."""
        finished = []
        for inflight in (self.image_inflight, self.video_inflight):
            done_ids = [tid for tid, t in inflight.items() if t.done()]
            for tid in done_ids:
                finished.append(inflight.pop(tid))
        return finished

    def all_inflight(self) -> list[asyncio.Task]:
        return [*self.image_inflight.values(), *self.video_inflight.values()]


async def _extract_provider(task: Dict[str, Any]) -> str:
    """Extract provider_id from a claimed task dict.

    优先从 payload 读取（兼容历史任务），否则从 ConfigResolver 解析。
    """
    payload = task.get("payload") or {}
    # 兼容已入队的历史任务（payload 中显式携带 provider）
    provider = payload.get("video_provider") or payload.get("image_provider")
    if provider:
        return _normalize_provider_id(provider)
    # 从全局/项目配置解析真实 provider
    project_name = task.get("project_name")
    if project_name:
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        resolver = ConfigResolver(async_session_factory)
        task_type = task.get("task_type", "")
        if task_type == "video":
            provider_id, _ = await resolver.default_video_backend()
            return provider_id
        else:
            provider_id, _ = await resolver.default_image_backend()
            return provider_id
    return DEFAULT_PROVIDER


def _normalize_provider_id(raw: str) -> str:
    """Normalize old-style provider names to registry provider_id."""
    mapping = {
        "gemini": "gemini-aistudio",
        "vertex": "gemini-vertex",
    }
    return mapping.get(raw, raw)


async def _load_pools_from_db() -> dict[str, ProviderPool]:
    """Load per-provider pool configs from ConfigService + PROVIDER_REGISTRY."""
    from lib.config.registry import PROVIDER_REGISTRY
    from lib.config.service import ConfigService
    from lib.db import safe_session_factory

    pools: dict[str, ProviderPool] = {}
    async with safe_session_factory() as session:
        svc = ConfigService(session)
        all_configs = await svc.get_all_provider_configs()
        for provider_id, meta in PROVIDER_REGISTRY.items():
            config = all_configs.get(provider_id, {})
            supports_image = "image" in meta.media_types
            supports_video = "video" in meta.media_types
            image_max = (
                int(config.get("image_max_workers", "5"))
                if supports_image
                else 0
            )
            video_max = (
                int(config.get("video_max_workers", "3"))
                if supports_video
                else 0
            )
            pools[provider_id] = ProviderPool(
                provider_id=provider_id,
                image_max=max(0, image_max),
                video_max=max(0, video_max),
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
        self.lease_ttl = max(1.0, float(TASK_WORKER_LEASE_TTL_SEC))
        self.heartbeat_interval = max(0.5, float(TASK_WORKER_HEARTBEAT_SEC))
        self.poll_interval = max(0.1, float(TASK_POLL_INTERVAL_SEC))

        self._main_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._owns_lease = False

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
    def _image_inflight(self) -> Dict[str, asyncio.Task]:
        """Merged view of all image inflight tasks (read-only convenience)."""
        merged: Dict[str, asyncio.Task] = {}
        for pool in self._pools.values():
            merged.update(pool.image_inflight)
        return merged

    @property
    def _video_inflight(self) -> Dict[str, asyncio.Task]:
        """Merged view of all video inflight tasks (read-only convenience)."""
        merged: Dict[str, asyncio.Task] = {}
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
        # Unknown provider — create a pool with conservative defaults
        pool = ProviderPool(
            provider_id=provider_id,
            image_max=1,
            video_max=1,
        )
        self._pools[provider_id] = pool
        logger.warning("为未知供应商 %s 创建默认池 (image=1, video=1)", provider_id)
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

        # Migrate inflight tasks to new pool objects
        for pid, new_pool in new_pools.items():
            old_pool = self._pools.get(pid)
            if old_pool:
                new_pool.image_inflight = old_pool.image_inflight
                new_pool.video_inflight = old_pool.video_inflight

        # Pools that existed before but are no longer registered:
        # keep them alive until their inflight tasks drain
        for pid, old_pool in self._pools.items():
            if pid not in new_pools and old_pool.all_inflight():
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

                # 仅在"新获得 lease 且本实例无在途任务"时回收 running 任务，
                # 避免 lease 短暂抖动时把自己正在执行的任务错误回队。
                all_inflight = self._image_inflight or self._video_inflight
                if self._owns_lease and not had_lease and not all_inflight:
                    await self.queue.requeue_running_tasks()

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

    async def _claim_tasks(self) -> bool:
        """Claim tasks from queue and route to per-provider pools.

        For each media_type, claim the next FIFO task. If the task's provider
        pool has room, dispatch it. If the pool is full, requeue it and stop
        claiming that media_type (since we'd keep getting the same task).
        """
        claimed_any = False

        for media_type in ("image", "video"):
            if not self._any_pool_has_room(media_type):
                continue

            while True:
                task = await self.queue.claim_next_task(media_type=media_type)
                if not task:
                    break

                provider_id = await _extract_provider(task)
                pool = self._get_or_create_pool(provider_id)

                if media_type == "image":
                    has_room = pool.has_image_room()
                else:
                    has_room = pool.has_video_room()

                if not has_room:
                    # Provider pool is full — requeue the task and stop
                    # claiming this media_type (FIFO means we'd get it again).
                    logger.info(
                        "供应商 %s 的 %s 池已满，任务 %s 放回队列",
                        provider_id, media_type, task["task_id"],
                    )
                    await self._requeue_single_task(task["task_id"])
                    break

                # Dispatch to pool
                claimed_any = True
                inflight = pool.image_inflight if media_type == "image" else pool.video_inflight
                inflight[task["task_id"]] = asyncio.create_task(
                    self._process_task(task),
                    name=f"generation-{media_type}-{task['task_id']}",
                )

                # Re-check if any pool still has room before trying next claim
                if not self._any_pool_has_room(media_type):
                    break

        return claimed_any

    async def _requeue_single_task(self, task_id: str) -> None:
        """Put a claimed (running) task back to queued status."""
        try:
            from lib.db import safe_session_factory
            from lib.db.models.task import Task
            from sqlalchemy import update
            from datetime import datetime, timezone

            async with safe_session_factory() as session:
                await session.execute(
                    update(Task)
                    .where(Task.task_id == task_id, Task.status == "running")
                    .values(
                        status="queued",
                        started_at=None,
                        updated_at=datetime.now(timezone.utc),
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
            for finished_task in pool.drain_finished():
                try:
                    await finished_task
                except Exception:
                    logger.debug("已处理的任务异常已在 _process_task 中记录")

    async def _wait_inflight_completion(self) -> None:
        pending_tasks = []
        for pool in self._pools.values():
            pending_tasks.extend(pool.all_inflight())
        if not pending_tasks:
            return
        await asyncio.gather(*pending_tasks, return_exceptions=True)
        for pool in self._pools.values():
            pool.image_inflight.clear()
            pool.video_inflight.clear()

    async def _process_task(self, task: Dict[str, Any]) -> None:
        task_id = task["task_id"]
        task_type = task.get("task_type", "unknown")
        provider_id = await _extract_provider(task)
        logger.info("开始处理任务 %s (type=%s, provider=%s)", task_id, task_type, provider_id)
        try:
            from server.services.generation_tasks import execute_generation_task

            result = await execute_generation_task(task)
            await self.queue.mark_task_succeeded(task_id, result)
            logger.info("任务完成 %s (type=%s, provider=%s)", task_id, task_type, provider_id)
        except Exception as exc:
            logger.exception("任务失败 %s (type=%s, provider=%s)", task_id, task_type, provider_id)
            await self.queue.mark_task_failed(task_id, str(exc))
