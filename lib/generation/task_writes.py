from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lib.generation.generation_queue import GenerationQueue
    from lib.project.task_project_claim import TaskProjectClaim

logger = logging.getLogger(__name__)


class TaskWrites:
    def __init__(self, queue: GenerationQueue, backoff: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.queue = queue
        self.backoff = backoff

    async def retry[T](self, task_id: str, write: Callable[[], Awaitable[T]]) -> T:
        for delay in (0.05, 0.2):
            try:
                return await write()
            except (DisconnectionError, InterfaceError, OperationalError, PoolTimeout, OSError):
                await self.backoff(delay)
        try:
            return await write()
        except Exception:
            logger.exception("任务状态写入失败 task_id=%s", task_id)
            raise

    async def failed(self, task_id: str, error: str) -> int:
        return await self.retry(task_id, lambda: self.queue.mark_task_failed(task_id, error))

    async def succeeded(
        self,
        task_id: str,
        result: dict[str, Any] | None,
        *,
        claim: TaskProjectClaim | None = None,
        expected_project_name: str | None = None,
    ) -> int | None:
        """返回受影响行数，项目认领作废或任务已改挂项目时返回 None。"""
        if claim is not None:
            expected_project_name = claim.project_name

        async def write() -> int | None:
            if claim is not None and claim.revoked:
                return None
            affected = await self.queue.mark_task_succeeded(
                task_id, result, expected_project_name=expected_project_name
            )
            if (
                affected == 0
                and expected_project_name is not None
                and await self.running_project_changed(task_id, expected_project_name)
            ):
                return None
            return affected

        return await self.retry(task_id, write)

    async def running_project_changed(self, task_id: str, expected_project_name: str) -> bool:
        row = await self.queue.get_task(task_id)
        return row is not None and row["status"] == "running" and row["project_name"] != expected_project_name
