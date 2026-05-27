"""
Async generation task queue shared by WebUI and skills.

Wraps TaskRepository with a module-level singleton pattern.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

from lib.db import safe_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.task_repo import TaskRepository

logger = logging.getLogger(__name__)


async def _derive_provider_id_for_enqueue(
    *,
    project_name: str | None,
    payload: dict[str, Any] | None,
    task_type: str,
    media_type: str,
) -> str | None:
    """入队时按 project + payload 派生 provider_id，供 claim SQL 池过滤使用。

    与 worker ``_extract_provider`` 同套解析逻辑，但失败时返回 ``None``（不强行
    回 DEFAULT_PROVIDER）——让任务走 ``provider_id IS NULL`` 兜底分支，由 worker
    claim 后做二次校验，比硬塞一个可能错误的 provider 安全。
    """
    is_video = media_type == "video" or task_type in ("video", "reference_video")
    try:
        from lib.config.resolver import ConfigResolver, get_project_manager
        from lib.db import async_session_factory

        project: dict | None = None
        if project_name:
            project = await asyncio.to_thread(get_project_manager().load_project, project_name)

        resolver = ConfigResolver(async_session_factory)
        if is_video:
            resolved = await resolver.resolve_video_backend(project, payload or {})
        else:
            resolved = await resolver.resolve_image_backend(project, payload or {}, capability="t2i")
    except Exception:
        logger.debug("入队时派生 provider_id 失败，留 NULL 由 worker 兜底", exc_info=True)
        return None
    return resolved.provider_id or None


ACTIVE_TASK_STATUSES = ("queued", "running", "cancelling")
TERMINAL_TASK_STATUSES = ("succeeded", "failed", "cancelled")
TASK_WORKER_LEASE_TTL_SEC = 10.0
TASK_WORKER_HEARTBEAT_SEC = 3.0
TASK_POLL_INTERVAL_SEC = 1.0

_QUEUE_LOCK = threading.Lock()
_QUEUE_INSTANCE: GenerationQueue | None = None


WorkerCancelCallback = Callable[[str], bool]


class GenerationQueue:
    """Async queue manager wrapping TaskRepository."""

    def __init__(
        self,
        *,
        session_factory=None,
    ):
        self._session_factory = session_factory or safe_session_factory
        # in-process callback to signal a running asyncio.Task to cancel;
        # set by server.app boot via set_worker_cancel_callback before worker.start()
        self._worker_cancel_callback: WorkerCancelCallback | None = None

    def set_worker_cancel_callback(self, callback: WorkerCancelCallback | None) -> None:
        """Attach in-process worker cancel callback. Must be called before worker.start()
        so cancel API can deliver signals synchronously (ADR 0006 秒级响应)."""
        self._worker_cancel_callback = callback

    async def enqueue_task(
        self,
        *,
        project_name: str,
        task_type: str,
        media_type: str,
        resource_id: str,
        payload: dict[str, Any] | None = None,
        script_file: str | None = None,
        source: str = "webui",
        dependency_task_id: str | None = None,
        dependency_group: str | None = None,
        dependency_index: int | None = None,
        user_id: str = DEFAULT_USER_ID,
        provider_id: str | None = None,
    ) -> dict[str, Any]:
        # caller 没传 provider_id → 入队时主动派生一次，让 claim 走 SQL 池过滤快路径；
        # 派生失败留 NULL，走 IS NULL 兜底，由 worker claim 后 _extract_provider 二次校验。
        if provider_id is None:
            provider_id = await _derive_provider_id_for_enqueue(
                project_name=project_name,
                payload=payload,
                task_type=task_type,
                media_type=media_type,
            )

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            result = await repo.enqueue(
                project_name=project_name,
                task_type=task_type,
                media_type=media_type,
                resource_id=resource_id,
                payload=payload,
                script_file=script_file,
                source=source,
                dependency_task_id=dependency_task_id,
                dependency_group=dependency_group,
                dependency_index=dependency_index,
                user_id=user_id,
                provider_id=provider_id,
            )
        if not result.get("deduped"):
            logger.info("任务入队 task_id=%s type=%s", result["task_id"], task_type)
        else:
            logger.debug("任务去重 task_id=%s", result["task_id"])
        return result

    async def claim_next_task(
        self,
        media_type: str,
        *,
        pool_full_providers: frozenset[str] | None = None,
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.claim_next(media_type, pool_full_providers=pool_full_providers)
        if task:
            logger.debug("任务被领取 task_id=%s", task["task_id"])
        return task

    async def requeue_running_tasks(self, *, limit: int = 1000) -> int:
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            recovered = await repo.requeue_running(limit=limit)
        if recovered > 0:
            logger.warning("回收 %d 个 running 任务", recovered)
        return recovered

    async def list_orphan_tasks_on_start(self) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.list_orphan_tasks_on_start()

    async def persist_provider_job_id(self, task_id: str, job_id: str) -> None:
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            await repo.persist_provider_job_id(task_id, job_id)

    async def mark_task_succeeded(self, task_id: str, result: dict[str, Any] | None) -> int:
        """Returns rows_affected (0 = 已被外部翻成非 running 终/中间态，worker 走 0-rows-cancelled 协议)."""
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            affected = await repo.mark_succeeded(task_id, result)
        if affected > 0:
            logger.info("任务成功 task_id=%s", task_id)
        else:
            logger.info("mark_succeeded 0 rows task_id=%s (已被外部翻状态)", task_id)
        return affected

    async def mark_task_failed(self, task_id: str, error_message: str) -> int:
        """Returns rows_affected (0 = 已被外部翻状态，worker 走 0-rows-cancelled 协议)."""
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            affected = await repo.mark_failed(task_id, error_message)
        if affected > 0:
            logger.warning("任务失败 task_id=%s error=%s", task_id, error_message[:200])
        else:
            logger.info("mark_failed 0 rows task_id=%s (已被外部翻状态)", task_id)
        return affected

    async def mark_task_cancelled(self, task_id: str, *, cancelled_by: str = "user") -> int:
        """Worker finally 0-rows-cancelled 协议兜底入口（SQL 守卫 status IN queued|cancelling）。"""
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.finalize_cancelled(task_id, cancelled_by=cancelled_by)

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            result = await repo.cancel_task(task_id)

        # Repository 返回 cancelling 意图列表 → GenerationQueue 同步分发 in-process 信号。
        # callback 同步调用：worker request_cancel 是 asyncio.Task.cancel()，O(1) 无 I/O。
        # 不用 asyncio.create_task fire-and-forget——会让 API 立刻返回但信号延迟到下次调度，
        # 破坏 ADR 0006 「秒级响应」。callback 不命中（task 已不在 inflight，
        # 例如 finally 阶段刚 pop）是 best-effort 失败：DB 已是 cancelling，worker
        # finally 走 mark_cancelled 兜底（SQL 守卫 IN ('queued','cancelling') 接住）。
        callback = self._worker_cancel_callback
        if callback is not None:
            for tid in result.get("cancelling", []):
                try:
                    callback(tid)
                except Exception:
                    logger.exception("worker cancel callback 派发失败 task_id=%s", tid)

        cancelled_count = len(result.get("cancelled", []))
        cancelling_count = len(result.get("cancelling", []))
        if cancelled_count or cancelling_count:
            logger.info(
                "任务取消 task_id=%s cancelled=%d cancelling=%d",
                task_id,
                cancelled_count,
                cancelling_count,
            )
        return result

    async def get_cancel_preview(self, task_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.get_cancel_preview(task_id)

    async def cancel_all_queued(self, project_name: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            result = await repo.cancel_all_queued(project_name)
        if result["cancelled_count"] > 0:
            logger.info("批量取消 project=%s 共取消 %d 个", project_name, result["cancelled_count"])
        return result

    async def get_cancel_all_preview(self, project_name: str) -> int:
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.get_cancel_all_preview(project_name)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.get(task_id)

    async def list_tasks(
        self,
        *,
        project_name: str | None = None,
        status: str | None = None,
        task_type: str | None = None,
        source: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.list_tasks(
                project_name=project_name,
                status=status,
                task_type=task_type,
                source=source,
                page=page,
                page_size=page_size,
            )

    async def get_task_stats(self, project_name: str | None = None) -> dict[str, int]:

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.get_stats(project_name=project_name)

    async def get_recent_tasks_snapshot(
        self,
        *,
        project_name: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.get_recent_tasks_snapshot(
                project_name=project_name,
                limit=limit,
            )

    async def get_events_since(
        self,
        *,
        last_event_id: int,
        project_name: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.get_events_since(
                last_event_id=last_event_id,
                project_name=project_name,
                limit=limit,
            )

    async def get_latest_event_id(self, *, project_name: str | None = None) -> int:

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.get_latest_event_id(project_name=project_name)

    async def acquire_or_renew_worker_lease(
        self,
        *,
        name: str,
        owner_id: str,
        ttl_seconds: float,
    ) -> bool:

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.acquire_or_renew_lease(
                name=name,
                owner_id=owner_id,
                ttl=ttl_seconds,
            )

    async def release_worker_lease(self, *, name: str, owner_id: str) -> None:

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            await repo.release_lease(name=name, owner_id=owner_id)

    async def is_worker_online(self, *, name: str = "default") -> bool:

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.is_worker_online(name=name)

    async def get_worker_lease(self, *, name: str = "default") -> dict[str, Any] | None:

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.get_worker_lease(name=name)


def get_generation_queue() -> GenerationQueue:
    global _QUEUE_INSTANCE
    if _QUEUE_INSTANCE is not None:
        return _QUEUE_INSTANCE

    with _QUEUE_LOCK:
        if _QUEUE_INSTANCE is None:
            _QUEUE_INSTANCE = GenerationQueue()
        return _QUEUE_INSTANCE


def read_queue_poll_interval() -> float:
    return max(0.1, float(TASK_POLL_INTERVAL_SEC))
