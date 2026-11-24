from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class TaskLease:
    ownership: TaskOwnership
    task_id: str
    released: asyncio.Event

    def release(self) -> None:
        self.ownership.release(self)


class TaskOwnership:
    def __init__(self) -> None:
        self._owners: dict[str, TaskLease] = {}

    def owned_task_ids(self) -> frozenset[str]:
        return frozenset(self._owners)

    def try_acquire(self, task_id: str) -> TaskLease | None:
        if task_id in self._owners:
            return None
        lease = TaskLease(self, task_id, asyncio.Event())
        self._owners[task_id] = lease
        return lease

    async def acquire(self, task_id: str) -> TaskLease:
        while (lease := self.try_acquire(task_id)) is None:
            await self._owners[task_id].released.wait()
        return lease

    def release(self, lease: TaskLease) -> None:
        if self._owners.get(lease.task_id) is lease:
            del self._owners[lease.task_id]
            lease.released.set()
