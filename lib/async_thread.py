"""Bridges for non-interruptible synchronous transactions in async workflows."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any, Self


class EventLoopBridge:
    """Run an async observation on the captured loop from a worker thread.

    File transactions sometimes need to hold a synchronous cross-process lock
    while consulting async database-backed state.  The transaction runs in a
    worker thread and uses this bridge so the observation happens inside the
    lock-held decision seam without blocking the event loop.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, loop_thread_id: int) -> None:
        self._loop = loop
        self._loop_thread_id = loop_thread_id

    @classmethod
    def capture(cls) -> Self:
        """Capture the currently running loop and its owner thread."""

        return cls(asyncio.get_running_loop(), threading.get_ident())

    def run[T](self, coroutine: Coroutine[Any, Any, T]) -> T:
        """Synchronously wait for *coroutine* from a non-loop worker thread."""

        if threading.get_ident() == self._loop_thread_id:
            coroutine.close()
            raise RuntimeError("EventLoopBridge.run must be called from a worker thread")
        if self._loop.is_closed():
            coroutine.close()
            raise RuntimeError("captured event loop is closed")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result()


async def run_noninterruptible_sync[**P, T](
    func: Callable[P, T],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """Finish one synchronous transaction even if its awaiting task is cancelled.

    A thread cannot be stopped safely after it starts mutating files.  Cancellation
    is therefore deferred until the caller's normal terminal-state gate can observe
    the completed result and compensate it when necessary.
    """

    task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


__all__ = ["EventLoopBridge", "run_noninterruptible_sync"]
