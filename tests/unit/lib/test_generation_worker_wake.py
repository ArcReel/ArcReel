import asyncio
from typing import Any

from lib.generation_worker import CapacityTable, GenerationWorker


class _WakeQueue:
    def __init__(self) -> None:
        self.idle = asyncio.Event()
        self.completed = asyncio.Event()
        self.claimed = False
        self.succeeded: list[tuple[str, dict[str, Any]]] = []

    async def acquire_or_renew_worker_lease(self, **_kwargs: Any) -> bool:
        return True

    async def release_worker_lease(self, **_kwargs: Any) -> None:
        return None

    async def list_orphan_tasks_on_start(self) -> list[dict[str, Any]]:
        return []

    async def claim_next_task(self, media_type: str, **_kwargs: Any) -> dict[str, str] | None:
        assert media_type == "text"
        if self.idle.is_set() and not self.claimed:
            self.claimed = True
            return {"task_id": "wake-task", "media_type": "text", "provider_id": "text"}
        self.idle.set()
        return None

    async def mark_task_succeeded(self, task_id: str, result: dict[str, Any]) -> int:
        self.succeeded.append((task_id, result))
        self.completed.set()
        return 1


async def test_wake_claims_task_without_waiting_for_poll_interval() -> None:
    queue = _WakeQueue()

    async def text_provider(_task: dict[str, Any]) -> str:
        return "text"

    async def execute(_task: dict[str, Any], *, claimed_provider_id: str) -> dict[str, bool]:
        assert claimed_provider_id == "text"
        return {"ok": True}

    worker = GenerationWorker(
        queue=queue,
        capacity=CapacityTable(_limits={}, _defaults={"text": 1}),
        provider_projection=text_provider,
        executor=execute,
        lanes=("text",),
    )
    worker.poll_interval = 60
    await worker.start()
    try:
        await asyncio.wait_for(queue.idle.wait(), timeout=1)
        worker.wake()
        await asyncio.wait_for(queue.completed.wait(), timeout=1)
        assert queue.succeeded == [("wake-task", {"ok": True})]
    finally:
        await worker.stop()
