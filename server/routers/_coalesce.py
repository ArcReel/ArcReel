"""Coalesce aggregate-read requests: at most one computation running and one waiting per key."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


class RequestCoalescer:
    """Combine concurrent requests with the same key into at most two computations.

    Start immediately when none is running. Otherwise all new requests share the sole waiting slot for the next computation.
    It starts only after the current computation finishes, reading inputs when it starts, so every request receives results
    no older than its arrival. On computation failure, all sharers receive the same exception; the key becomes available
    and the next request computes again.

    In-process only, without caching results across requests. Keys must include all request dimensions affecting responses
    (at least language). Run computation in a separate task and await through ``asyncio.shield``: cancellation of one request
    (client disconnect) affects only that request; shared computation and other waiters still receive results normally."""

    def __init__(self) -> None:
        self._running: dict[Hashable, asyncio.Task[Any]] = {}
        self._waiting: dict[Hashable, asyncio.Task[Any]] = {}

    async def run[T](self, key: Hashable, compute: Callable[[], Awaitable[T]]) -> T:
        task = self._waiting.get(key)
        if task is None:
            previous = self._running.get(key)
            task = asyncio.create_task(self._compute(key, previous, compute))
            # All requests may have disconnected without retrieving the exception; retrieve it to avoid "never retrieved" on task collection.
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            (self._running if previous is None else self._waiting)[key] = task
        return await asyncio.shield(task)

    async def _compute[T](
        self, key: Hashable, previous: asyncio.Task[Any] | None, compute: Callable[[], Awaitable[T]]
    ) -> T:
        me = asyncio.current_task()
        try:
            if previous is not None:
                await asyncio.wait([previous])
                # Promote the waiting slot; later arrivals create a new waiting slot to read newer input.
                self._running[key] = self._waiting.pop(key)
            return await compute()
        finally:
            for slots in (self._running, self._waiting):
                if slots.get(key) is me:
                    del slots[key]
