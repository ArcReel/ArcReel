from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lib.generation.generation_queue import GenerationQueue

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

    async def succeeded(self, task_id: str, result: dict[str, Any] | None) -> int:
        return await self.retry(task_id, lambda: self.queue.mark_task_succeeded(task_id, result))
