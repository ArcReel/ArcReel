"""Parameterized subscription broadcaster: subscribe/unsubscribe, broadcast, idle heartbeats, and overflow handling.

Session streams (SessionManager) and project event streams (ProjectEventService) share this component for
SSE fanout, with every semantic difference expressed through parameters:

- Overflow policy: session streams use :class:`EvictNonCriticalAndSignal` (critical messages evict one
  noncritical message; if still full, clear the queue and end that subscriber's stream, signaling reconnection),
  while project event streams use :class:`DropSubscriber` (on full queues, remove subscribers and end their streams;
  clients realign through initial snapshots after reconnecting).
- Optional first/last-subscriber lifecycle hooks (project events use them to start/stop background scanning).

Preambles (session buffered replay and project-event initial snapshots) stay outside this component: consumers ensure subscription/preamble
atomicity through locks / synchronous critical sections (see docs/adr/0046).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


class _IdleMarker:
    """Idle heartbeat marker emitted by :meth:`SseChannel.iterate` after no message within idle_timeout.

    Consumers map it to their own heartbeat events (session ``Heartbeat``, project-event
    ``{"type": "_idle"}``) and check for disconnection on it. Compare identity with singleton ``IDLE``.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "IDLE"


IDLE = _IdleMarker()

# Overflow sentinel: injected after the overflow policy clears a removed subscriber's queue; iterate ends the stream on it
# (stream end signals reconnection). Invisible outside the component; consumers see only stream completion.
_END_OF_STREAM = object()


def _end_stream(queue: asyncio.Queue) -> None:
    """Clear *queue* and inject an overflow signal so consumption terminates instead of blocking forever."""
    while not queue.empty():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    # Should not remain full after clearing
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(_END_OF_STREAM)


class EvictNonCriticalAndSignal:
    """Overflow policy: critical messages evict one noncritical message; if still full, end that subscriber's stream.

    ``is_critical`` determines criticality. On a full queue, silently drop noncritical messages (retain
    the subscriber); for critical messages, evict one noncritical entry and retry. If still full (all queued messages are
    critical), the subscriber is irrecoverably behind: clear its queue and inject an overflow signal.
    :meth:`SseChannel.iterate` ends on the signal; consumers recover by reconnecting.
    """

    def __init__(self, *, is_critical: Callable[[Any], bool]) -> None:
        self._is_critical = is_critical

    def deliver(self, queue: asyncio.Queue, item: Any) -> bool:
        try:
            queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            if not self._is_critical(item):
                return True  # Dropping noncritical messages is acceptable
        # Critical message with full queue: evict one noncritical message to make room.
        self._evict_one_non_critical(queue)
        try:
            queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            return False

    def finalize_removal(self, queue: asyncio.Queue) -> None:
        _end_stream(queue)

    def on_removed(self, count: int) -> None:
        pass

    def _evict_one_non_critical(self, queue: asyncio.Queue) -> None:
        """Remove one noncritical message from *queue* and restore the rest in original order."""
        temp: list[Any] = []
        evicted = False
        while not queue.empty():
            try:
                msg = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not evicted and not self._is_critical(msg):
                evicted = True  # Drop this one
                continue
            temp.append(msg)
        for msg in temp:
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                break


class DropSubscriber:
    """Overflow policy: a full queue removes the subscriber and ends its stream.

    Without stream completion, clients receive only idle heartbeats and never reconnect, silently losing all later events.
    After completion, clients reconnect and realign through initial snapshots, discarding backlog. ``on_removed`` receives
    the count removed during one broadcast (for logging).
    """

    def __init__(self, *, on_removed: Callable[[int], None] | None = None) -> None:
        self._on_removed = on_removed

    def deliver(self, queue: asyncio.Queue, item: Any) -> bool:
        try:
            queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            return False

    def finalize_removal(self, queue: asyncio.Queue) -> None:
        _end_stream(queue)

    def on_removed(self, count: int) -> None:
        if self._on_removed is not None:
            self._on_removed(count)


SseOverflowPolicy = EvictNonCriticalAndSignal | DropSubscriber


class SseChannel:
    """A registry of subscription queues plus broadcast fanout.

    ``subscribe`` runs synchronously so consumers can prepare preamble snapshots and
    register subscriptions in one synchronous critical section, ensuring seamless replay/live transition (preambles themselves stay outside).
    """

    def __init__(
        self,
        *,
        overflow: SseOverflowPolicy,
        queue_maxsize: int = 100,
        on_first_subscriber: Callable[[], None] | None = None,
        on_last_subscriber: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._overflow = overflow
        self._queue_maxsize = queue_maxsize
        self._on_first_subscriber = on_first_subscriber
        self._on_last_subscriber = on_last_subscriber
        self._subscribers: set[asyncio.Queue] = set()

    @property
    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue:
        """Register and return a new subscription queue.

        Trigger ``on_first_subscriber`` if the set was empty before registration (call only after registration,
        so hooks see the new subscriber; project-event background scanning checks liveness with has_subscribers).
        If the hook raises, roll back the new queue before propagating: callers never receive the queue and cannot unsubscribe,
        so it must not remain in the set.
        """
        was_empty = not self._subscribers
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        self._subscribers.add(queue)
        if was_empty and self._on_first_subscriber is not None:
            try:
                self._on_first_subscriber()
            except BaseException:
                self._subscribers.discard(queue)
                raise
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a subscription queue; if the set is empty afterward, trigger and await ``on_last_subscriber``.

        Trigger on an empty post-removal set rather than a strict 1→0 transition: subscribers removed for overflow can later
        unsubscribe and still trigger cleanup (overflow removal itself invokes no hook); hook implementations must be idempotent.
        """
        self._subscribers.discard(queue)
        if not self._subscribers and self._on_last_subscriber is not None:
            await self._on_last_subscriber()

    def unsubscribe_nowait(self, queue: asyncio.Queue) -> bool:
        """Synchronously remove a queue without lifecycle hooks; return whether no subscribers remain.

        For cleanup paths that cannot ``await`` (such as cancellation handlers, where another cancellation may interrupt await
        and leave cleanup incomplete); callers handle background-task cleanup themselves.
        """
        self._subscribers.discard(queue)
        return not self._subscribers

    def broadcast(self, item: Any) -> None:
        """Deliver *item* to all subscription queues; remove failed-delivery subscribers according to the overflow policy."""
        stale: list[asyncio.Queue] = [queue for queue in self._subscribers if not self._overflow.deliver(queue, item)]
        for queue in stale:
            self._overflow.finalize_removal(queue)
            self._subscribers.discard(queue)
        if stale:
            self._overflow.on_removed(len(stale))

    async def iterate(self, queue: asyncio.Queue, *, idle_timeout: float) -> AsyncIterator[Any]:
        """Turn a subscription queue into an async iterator: yield broadcast items individually and :data:`IDLE` when idle.

        This generator deliberately has no ``finally`` cleanup: the consumer's async CM ``__aexit__`` performs unsubscription
        deterministically (see docs/adr/0005). Bare-generator finally runs only during GC, precisely the
        leak path this design avoids.
        """
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=idle_timeout)
            except TimeoutError:
                yield IDLE
                continue
            if item is _END_OF_STREAM:
                return
            yield item
