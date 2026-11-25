"""队列文本任务的供应商记录与脱敏生命周期事件。"""

import json
import logging
import time
from datetime import datetime
from typing import Any

from lib.db.base import as_utc
from lib.generation.generation_queue import GenerationQueue
from lib.generation.task_writes import TaskWrites

logger = logging.getLogger(__name__)


class TextTaskLifecycle:
    def __init__(self, task: dict[str, Any], queue: GenerationQueue, writes: TaskWrites):
        self.task_id = task["task_id"]
        self.task_type = task.get("task_type")
        self.provider_id: str | None = None
        self.queue = queue
        self.writes = writes
        self.started = time.monotonic()
        self.queue_ms = None
        if task.get("queued_at") and task.get("started_at"):
            elapsed = as_utc(datetime.fromisoformat(task["started_at"])) - as_utc(
                datetime.fromisoformat(task["queued_at"])
            )
            self.queue_ms = max(0, round(elapsed.total_seconds() * 1000))

    async def record_provider(self, provider_id: str) -> None:
        if provider_id != self.provider_id:
            await self.writes.retry(
                self.task_id, lambda: self.queue.persist_execution_provider_id(self.task_id, provider_id)
            )
            self.provider_id = provider_id
            self.log("provider_resolved")

    def log(self, status: str, failure_code: str | None = None) -> None:
        event = {
            "event": "text_task",
            "task_id": self.task_id,
            "task_type": self.task_type,
            "provider_id": self.provider_id,
            "status": status,
            "queue_ms": self.queue_ms,
            "execution_ms": round((time.monotonic() - self.started) * 1000),
            "failure_code": failure_code,
        }
        logger.log(logging.WARNING if failure_code else logging.INFO, "%s", json.dumps(event, separators=(",", ":")))
