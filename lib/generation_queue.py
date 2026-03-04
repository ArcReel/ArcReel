"""
Async generation task queue shared by WebUI and skills.

Wraps TaskRepository with a module-level singleton pattern.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from lib.db import async_session_factory, init_db
from lib.db.repositories.task_repo import TaskRepository

logger = logging.getLogger(__name__)

ACTIVE_TASK_STATUSES = ("queued", "running")
TERMINAL_TASK_STATUSES = ("succeeded", "failed")
TASK_WORKER_LEASE_TTL_SEC = 10.0
TASK_WORKER_HEARTBEAT_SEC = 3.0
TASK_POLL_INTERVAL_SEC = 1.0

_QUEUE_LOCK = threading.Lock()
_QUEUE_INSTANCE: Optional["GenerationQueue"] = None


class GenerationQueue:
    """Async queue manager wrapping TaskRepository."""

    def __init__(
        self,
        *,
        session_factory=None,
        _skip_init_db: bool = False,
    ):
        self._session_factory = session_factory or async_session_factory
        self._skip_init_db = _skip_init_db
        self._db_initialized = _skip_init_db

    async def _ensure_db(self) -> None:
        if not self._db_initialized:
            await init_db()
            self._db_initialized = True

    async def enqueue_task(
        self,
        *,
        project_name: str,
        task_type: str,
        media_type: str,
        resource_id: str,
        payload: Optional[Dict[str, Any]] = None,
        script_file: Optional[str] = None,
        source: str = "webui",
        dependency_task_id: Optional[str] = None,
        dependency_group: Optional[str] = None,
        dependency_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        await self._ensure_db()
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
            )
        if not result.get("deduped"):
            logger.info("任务入队 task_id=%s type=%s", result["task_id"], task_type)
        else:
            logger.debug("任务去重 task_id=%s", result["task_id"])
        return result

    async def claim_next_task(self, media_type: str) -> Optional[Dict[str, Any]]:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.claim_next(media_type)
        if task:
            logger.debug("任务被领取 task_id=%s", task["task_id"])
        return task

    async def requeue_running_tasks(self, *, limit: int = 1000) -> int:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            recovered = await repo.requeue_running(limit=limit)
        if recovered > 0:
            logger.warning("回收 %d 个 running 任务", recovered)
        return recovered

    async def mark_task_succeeded(
        self, task_id: str, result: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.mark_succeeded(task_id, result)
        if task:
            logger.info("任务成功 task_id=%s", task_id)
        return task

    async def mark_task_failed(
        self, task_id: str, error_message: str
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.mark_failed(task_id, error_message)
        if task:
            logger.warning("任务失败 task_id=%s error=%s", task_id, error_message[:200])
        return task

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.get(task_id)

    async def list_tasks(
        self,
        *,
        project_name: Optional[str] = None,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        source: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        await self._ensure_db()
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

    async def get_task_stats(
        self, project_name: Optional[str] = None
    ) -> Dict[str, int]:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.get_stats(project_name=project_name)

    async def get_recent_tasks_snapshot(
        self,
        *,
        project_name: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.get_recent_tasks_snapshot(
                project_name=project_name, limit=limit,
            )

    async def get_events_since(
        self,
        *,
        last_event_id: int,
        project_name: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.get_events_since(
                last_event_id=last_event_id,
                project_name=project_name,
                limit=limit,
            )

    async def get_latest_event_id(
        self, *, project_name: Optional[str] = None
    ) -> int:
        await self._ensure_db()
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
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.acquire_or_renew_lease(
                name=name, owner_id=owner_id, ttl=ttl_seconds,
            )

    async def release_worker_lease(self, *, name: str, owner_id: str) -> None:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            await repo.release_lease(name=name, owner_id=owner_id)

    async def is_worker_online(self, *, name: str = "default") -> bool:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            return await repo.is_worker_online(name=name)

    async def get_worker_lease(
        self, *, name: str = "default"
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_db()
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
