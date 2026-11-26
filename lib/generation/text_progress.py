"""Process-local progress for running text tasks, shared with the active snapshot."""

import asyncio
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from lib.backends.text_backends.base import TextProgress
from lib.backends.text_backends.streaming import TextTaskContext, bind_text_task


class TaskProgressTable:
    def __init__(self, *, scheduler: asyncio.AbstractEventLoop | None = None) -> None:
        self._scheduler = scheduler
        self._latest: dict[str, dict[str, Any]] = {}
        self.publish: Callable[[str, dict[str, Any]], None] | None = None

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._latest.get(task_id)

    @contextmanager
    def track(self, project: str, task_id: str, task_type: str) -> Generator[Callable[[TextProgress], None]]:
        loop = self._scheduler or asyncio.get_running_loop()
        last_sent = 0.0
        timer: asyncio.TimerHandle | None = None
        active = True

        def cancel_timer() -> None:
            nonlocal timer
            if timer is not None:
                timer.cancel()
                timer = None

        def flush() -> None:
            nonlocal last_sent
            cancel_timer()
            last_sent = loop.time()
            if self.publish:
                self.publish(project, self._latest[task_id])

        def report(progress: TextProgress) -> None:
            nonlocal timer
            if not active:
                return
            previous = self._latest.get(task_id, {})
            payload = {"phase": progress.phase, "task_id": task_id, "seq": previous.get("seq", 0) + 1}
            if progress.phase == "waiting":
                payload["model"] = progress.model
            elif progress.phase == "retrying":
                payload.update(error=progress.error, attempt=progress.attempt, max_attempts=progress.max_attempts)
            else:
                payload.update(characters=progress.characters, thinking=progress.thinking and not progress.characters)
            self._latest[task_id] = payload
            if (
                progress.phase != "receiving"
                or previous.get("phase") != "receiving"
                or previous["thinking"] != payload["thinking"]
                or loop.time() >= last_sent + 2
            ):
                flush()
            elif timer is None:
                timer = loop.call_at(last_sent + 2, flush)

        try:
            with bind_text_task(TextTaskContext(task_id, task_type, report)):
                yield report
        finally:
            active = False
            cancel_timer()
            self._latest.pop(task_id, None)
