"""SseChannel 参数化订阅广播组件的行为测试。

直接断言订阅/广播/溢出/生命周期钩子的可见效果；两种溢出策略
（会话流「逐出非关键 + 溢出信号」、项目事件流「移除订阅者」）分别覆盖。
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
        # 心跳后继续等待，直播消息照常送达。
        channel.broadcast({"type": "assistant", "uuid": "after-idle"})
        assert (await anext(stream))["uuid"] == "after-idle"
        await stream.aclose()


class TestEvictNonCriticalAndSignalOverflow:
    """会话流溢出策略：逐出非关键消息 + 溢出信号（流结束即重连信号）。"""

    async def test_full_queue_drops_non_critical_message_silently(self):
        channel = _session_channel(queue_maxsize=1)
        queue = channel.subscribe()
        channel.broadcast({"type": "assistant", "uuid": "keep"})

        channel.broadcast({"type": "stream_event", "uuid": "dropped"})

        # 非关键消息静默丢弃，订阅者保留，队列内容不变。
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

        # 订阅者被移除，队列被清空后注入溢出信号：流直接结束，无溢出事件泄漏。
        assert channel.subscriber_count == 0
        received = [item async for item in channel.iterate(queue, idle_timeout=1.0)]
        assert received == []


class TestDropSubscriberOverflow:
    """项目事件流溢出策略：队列满移除订阅者，无溢出信号。"""

    async def test_full_queue_removes_subscriber_and_reports_count(self):
        removed_counts: list[int] = []
        channel = SseChannel(
            overflow=DropSubscriber(on_removed=removed_counts.append),
            queue_maxsize=1,
        )
        stale = channel.subscribe()
        healthy = channel.subscribe()
        channel.broadcast(("changes", {"batch_id": "b1"}))
        healthy.get_nowait()  # stale 不消费，healthy 跟上节奏

        channel.broadcast(("changes", {"batch_id": "b2"}))

        assert channel.subscriber_count == 1
        assert removed_counts == [1]
        assert healthy.get_nowait() == ("changes", {"batch_id": "b2"})
        # 被移除订阅者的队列只剩溢出前的内容——不注入任何信号。
        assert stale.get_nowait() == ("changes", {"batch_id": "b1"})
        assert stale.empty()

    async def test_removed_subscriber_stream_does_not_end(self):
        channel = SseChannel(overflow=DropSubscriber(), queue_maxsize=1)
        stale = channel.subscribe()
        channel.broadcast(("changes", {"batch_id": "b1"}))
        channel.broadcast(("changes", {"batch_id": "b2"}))  # 溢出 → 移除
        assert channel.subscriber_count == 0

        # 流不结束：消费完存量后靠空闲心跳维持，断线由消费方自检。
        stream = channel.iterate(stale, idle_timeout=0.02)
        assert await anext(stream) == ("changes", {"batch_id": "b1"})
        assert await asyncio.wait_for(anext(stream), timeout=1.0) is IDLE
        await stream.aclose()


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
        assert events == ["first"]  # 1→2 不触发

        await channel.unsubscribe(first)
        assert events == ["first"]  # 还剩订阅者，末位钩子不触发

    async def test_first_subscriber_hook_sees_registered_queue(self):
        counts: list[int] = []
        channel = SseChannel(
            overflow=DropSubscriber(),
            on_first_subscriber=lambda: counts.append(channel.subscriber_count),
        )
        channel.subscribe()
        # 钩子触发时队列已注册——项目事件流的后台扫描以 has_subscribers 判存活。
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
        channel.broadcast("b")  # 溢出 → 移除最后一个订阅者

        assert channel.subscriber_count == 0
        assert events == ["first"]  # 溢出移除不触发生命周期钩子

        # 被溢出移除的订阅者事后正常退订：按「移除后集合为空」触发收尾。
        await channel.unsubscribe(stale)
        assert events == ["first", "last"]

    async def test_unsubscribe_nowait_skips_hooks_and_reports_emptiness(self):
        events: list[str] = []
        channel = self._channel_with_hooks(events)
        first = channel.subscribe()
        second = channel.subscribe()

        assert channel.unsubscribe_nowait(first) is False
        assert channel.unsubscribe_nowait(second) is True
        assert events == ["first"]  # 不触发末位钩子，收尾由调用方自理

    async def test_first_subscriber_hook_exception_propagates_and_unwinds_queue(self):
        def _boom() -> None:
            raise RuntimeError("hook failed")

        channel = SseChannel(overflow=DropSubscriber(), on_first_subscriber=_boom)

        with pytest.raises(RuntimeError, match="hook failed"):
            channel.subscribe()

        # 钩子抛异常时新注册的队列回退，订阅集合复原到调用前状态。
        assert not channel.has_subscribers
        assert channel.subscriber_count == 0
