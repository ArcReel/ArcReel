"""SseChannel parameterized subscription/broadcast behavior.

Assert observable subscription/broadcast/overflow/lifecycle effects; cover both overflow policies:
session streams evict noncritical messages plus signal overflow; project events remove subscribers.
"""

import asyncio

import pytest

from server.sse_channel import IDLE, DropSubscriber, EvictNonCriticalAndSignal, SseChannel


def _is_critical(message) -> bool:
    return message.get("type") in {"result", "runtime_status", "user", "assistant"}


def _session_channel(*, queue_maxsize: int = 100) -> SseChannel:
    return SseChannel(
        overflow=EvictNonCriticalAndSignal(is_critical=_is_critical),
        queue_maxsize=queue_maxsize,
    )


class TestSubscribeBroadcast:
    async def test_broadcast_reaches_all_subscribers(self):
        channel = SseChannel(overflow=DropSubscriber())
        first = channel.subscribe()
        second = channel.subscribe()

        channel.broadcast(("changes", {"batch_id": "b1"}))

        assert first.get_nowait() == ("changes", {"batch_id": "b1"})
        assert second.get_nowait() == ("changes", {"batch_id": "b1"})

    async def test_unsubscribed_queue_stops_receiving(self):
        channel = SseChannel(overflow=DropSubscriber())
        queue = channel.subscribe()
        remaining = channel.subscribe()

        await channel.unsubscribe(queue)
        channel.broadcast({"type": "assistant"})

        assert queue.empty()
        assert remaining.get_nowait() == {"type": "assistant"}

    async def test_subscriber_count_tracks_membership(self):
        channel = SseChannel(overflow=DropSubscriber())
        assert not channel.has_subscribers

        queue = channel.subscribe()
        assert channel.has_subscribers
        assert channel.subscriber_count == 1

        await channel.unsubscribe(queue)
        assert not channel.has_subscribers
        assert channel.subscriber_count == 0


class TestIterate:
    async def test_iterate_yields_broadcast_items_in_order(self):
        channel = SseChannel(overflow=DropSubscriber())
        queue = channel.subscribe()
        channel.broadcast({"type": "assistant", "uuid": "m1"})
        channel.broadcast({"type": "assistant", "uuid": "m2"})

        stream = channel.iterate(queue, idle_timeout=1.0)
        assert await anext(stream) == {"type": "assistant", "uuid": "m1"}
        assert await anext(stream) == {"type": "assistant", "uuid": "m2"}
        await stream.aclose()

    async def test_iterate_yields_idle_marker_on_timeout(self):
        channel = SseChannel(overflow=DropSubscriber())
        queue = channel.subscribe()

        stream = channel.iterate(queue, idle_timeout=0.02)
        assert await asyncio.wait_for(anext(stream), timeout=1.0) is IDLE
        # Keep waiting after heartbeats; live messages arrive normally.
        channel.broadcast({"type": "assistant", "uuid": "after-idle"})
        assert (await anext(stream))["uuid"] == "after-idle"
        await stream.aclose()


class TestEvictNonCriticalAndSignalOverflow:
    """Session overflow evicts noncritical messages and signals overflow; stream termination signals reconnection."""

    async def test_full_queue_drops_non_critical_message_silently(self):
        channel = _session_channel(queue_maxsize=1)
        queue = channel.subscribe()
        channel.broadcast({"type": "assistant", "uuid": "keep"})

        channel.broadcast({"type": "stream_event", "uuid": "dropped"})

        # Silently discard noncritical messages, retaining subscribers and queue contents.
        assert channel.subscriber_count == 1
        assert queue.get_nowait()["uuid"] == "keep"
        assert queue.empty()

    async def test_critical_message_evicts_one_non_critical_to_fit(self):
        channel = _session_channel(queue_maxsize=2)
        queue = channel.subscribe()
        channel.broadcast({"type": "stream_event", "uuid": "victim"})
        channel.broadcast({"type": "assistant", "uuid": "kept"})

        channel.broadcast({"type": "result", "uuid": "critical"})

        assert channel.subscriber_count == 1
        remaining = [queue.get_nowait()["uuid"] for _ in range(2)]
        assert remaining == ["kept", "critical"]

    async def test_all_critical_full_queue_ends_stream_and_removes_subscriber(self):
        channel = _session_channel(queue_maxsize=2)
        queue = channel.subscribe()
        channel.broadcast({"type": "result", "uuid": "c1"})
        channel.broadcast({"type": "user", "uuid": "c2"})

        channel.broadcast({"type": "assistant", "uuid": "no-room"})

        # Remove subscribers, clear queues, inject overflow signals; streams end without leaking overflow events.
        assert channel.subscriber_count == 0
        received = [item async for item in channel.iterate(queue, idle_timeout=1.0)]
        assert received == []


class TestDropSubscriberOverflow:
    """Project-event overflow removes full-queue subscribers and ends streams; reconnections recover through snapshots."""

    async def test_full_queue_removes_subscriber_and_reports_count(self):
        removed_counts: list[int] = []
        channel = SseChannel(
            overflow=DropSubscriber(on_removed=removed_counts.append),
            queue_maxsize=1,
        )
        channel.subscribe()  # No consumption; second broadcast overflows.
        healthy = channel.subscribe()
        channel.broadcast(("changes", {"batch_id": "b1"}))
        healthy.get_nowait()  # healthy keeps pace.

        channel.broadcast(("changes", {"batch_id": "b2"}))

        assert channel.subscriber_count == 1
        assert removed_counts == [1]
        assert healthy.get_nowait() == ("changes", {"batch_id": "b2"})

    async def test_removed_subscriber_stream_ends(self):
        """Without termination, clients receive idle heartbeats but never reconnect, silently losing all later one-shot events."""
        channel = SseChannel(overflow=DropSubscriber(), queue_maxsize=1)
        stale = channel.subscribe()
        channel.broadcast(("changes", {"batch_id": "b1"}))
        channel.broadcast(("changes", {"batch_id": "b2"}))  # Overflow → removal.
        assert channel.subscriber_count == 0

        # Discard backlog; reconnection snapshots realign state.
        async def _drain() -> list:
            return [item async for item in channel.iterate(stale, idle_timeout=0.02)]

        assert await asyncio.wait_for(_drain(), timeout=1.0) == []


class TestLifecycleHooks:
    def _channel_with_hooks(self, events: list[str], **kwargs) -> SseChannel:
        async def _on_last() -> None:
            events.append("last")

        return SseChannel(
            overflow=DropSubscriber(),
            on_first_subscriber=lambda: events.append("first"),
            on_last_subscriber=_on_last,
            **kwargs,
        )

    async def test_first_subscriber_hook_fires_only_on_empty_to_one(self):
        events: list[str] = []
        channel = self._channel_with_hooks(events)

        first = channel.subscribe()
        assert events == ["first"]
        channel.subscribe()
        assert events == ["first"]  # 1→2 does not trigger.

        await channel.unsubscribe(first)
        assert events == ["first"]  # Subscribers remain, so no last-subscriber hook.

    async def test_first_subscriber_hook_sees_registered_queue(self):
        counts: list[int] = []
        channel = SseChannel(
            overflow=DropSubscriber(),
            on_first_subscriber=lambda: counts.append(channel.subscriber_count),
        )
        channel.subscribe()
        # Register queues before hooks; project-event scans use has_subscribers for liveness.
        assert counts == [1]

    async def test_last_subscriber_hook_awaited_when_set_becomes_empty(self):
        events: list[str] = []
        channel = self._channel_with_hooks(events)
        queue = channel.subscribe()

        await channel.unsubscribe(queue)

        assert events == ["first", "last"]

    async def test_overflow_removal_does_not_fire_last_subscriber_hook(self):
        events: list[str] = []
        channel = self._channel_with_hooks(events, queue_maxsize=1)
        stale = channel.subscribe()
        channel.broadcast("a")
        channel.broadcast("b")  # Overflow removes the final subscriber.

        assert channel.subscriber_count == 0
        assert events == ["first"]  # Overflow removal does not trigger lifecycle hooks.

        # Later normal unsubscription after overflow removal triggers cleanup because the remaining set is empty.
        await channel.unsubscribe(stale)
        assert events == ["first", "last"]

    async def test_unsubscribe_nowait_skips_hooks_and_reports_emptiness(self):
        events: list[str] = []
        channel = self._channel_with_hooks(events)
        first = channel.subscribe()
        second = channel.subscribe()

        assert channel.unsubscribe_nowait(first) is False
        assert channel.unsubscribe_nowait(second) is True
        assert events == ["first"]  # Suppress last-subscriber hooks; callers handle cleanup.

    async def test_first_subscriber_hook_exception_propagates_and_unwinds_queue(self):
        def _boom() -> None:
            raise RuntimeError("hook failed")

        channel = SseChannel(overflow=DropSubscriber(), on_first_subscriber=_boom)

        with pytest.raises(RuntimeError, match="hook failed"):
            channel.subscribe()

        # Hook failures roll back new queues, restoring the previous subscriber set.
        assert not channel.has_subscribers
        assert channel.subscriber_count == 0
