"""Cancellation during post-provider writes: observe task/write-thread interleavings.

Per docs/standards/backend.md post-provider noninterruptible settlement rules, writes complete
and cancellation is consumed, allowing later transcoding/commits. Gate write threads, cancel
during writes, then release and assert normal completion plus subsequent work.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path


class WriteGate:
    """Write-thread gate sets ``started`` on entry, continuing only after ``release``."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def block(self) -> None:
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError("写盘闸门未放行")


class GatedWritePath(type(Path())):
    """``output_path`` whose ``write_bytes`` is gated.

    Inject as request input objects, never patch production symbols.
    """

    def attach(self, gate: WriteGate) -> GatedWritePath:
        self._gate = gate
        return self

    def write_bytes(self, data) -> int:
        self._gate.block()
        return super().write_bytes(data)

    def with_name(self, name: str) -> GatedWritePath:
        return super().with_name(name).attach(self._gate)


async def cancel_during_write(operation: Callable[[], Awaitable[object]], gate: WriteGate) -> list[str]:
    """Run ``operation``, cancel while its write thread is gated, then release and return subsequent-step records.

    Append after ``operation`` a subsequent-step marker representing caller transcoding/commits. If cancellation
    escapes persistence, tasks end with ``CancelledError``, propagated here without that marker.
    """
    steps: list[str] = []

    async def settle_then_commit() -> None:
        await operation()
        steps.append("follow-up")

    task = asyncio.create_task(settle_then_commit())
    try:
        assert await asyncio.to_thread(gate.started.wait, 5), "写盘线程未启动"
        task.cancel()
        # Thread round trips return control to loops; interruptible implementations end here.
        await asyncio.to_thread(lambda: None)
        assert not task.done(), "取消不得在写盘结算前结束任务"
    finally:
        gate.release.set()
    await asyncio.wait_for(task, 5)
    return steps
