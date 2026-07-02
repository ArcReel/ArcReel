"""参数化订阅广播组件：订阅/退订、广播、空闲心跳、溢出处理。

会话流（SessionManager）与项目事件流（ProjectEventService）共用本组件做
SSE fanout，两处的语义差异全部经参数表达：

- 溢出策略：会话流用 :class:`EvictNonCriticalAndSignal`（关键消息挤掉一条
  非关键消息；仍满则清空队列并结束该订阅者的流——流结束即重连信号），
  项目事件流用 :class:`DropSubscriber`（队列满即移除订阅者，无溢出信号，
  断线由消费方心跳自检发现）。
- 首/末订阅者生命周期钩子可选（项目事件流用于启停后台扫描）。

开场白（会话流的缓冲回放、项目事件流的初始快照）不进组件：订阅与开场白
的原子性由消费方持锁/同步临界区保证（见 docs/adr/0046）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


class _IdleMarker:
    """空闲心跳标记：idle_timeout 内无消息时由 :meth:`SseChannel.iterate` 产出。

    消费方将其映射为各自的心跳事件（会话流 ``Heartbeat``、项目事件流
    ``{"type": "_idle"}``），并在其上执行断线自检。用单例 ``IDLE`` 做身份比较。
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "IDLE"


IDLE = _IdleMarker()

# 溢出信号哨兵：EvictNonCriticalAndSignal 清空溢出队列后注入，iterate 遇之
# 即结束流（流结束即重连信号）。对组件外不可见——消费方看到的只是流结束。
_END_OF_STREAM = object()


class EvictNonCriticalAndSignal:
    """溢出策略：关键消息挤掉一条非关键消息；仍满则结束该订阅者的流。

    ``is_critical`` 判定消息关键性。队列满时非关键消息静默丢弃（订阅者
    保留）；关键消息先逐出队内一条非关键消息腾位重试；重试仍满（队内全是
    关键消息）说明订阅者已无可救药地落后——清空其队列并注入溢出信号，
    :meth:`SseChannel.iterate` 遇信号即结束流，消费方以重连恢复。
    """

    def __init__(self, *, is_critical: Callable[[Any], bool]) -> None:
        self._is_critical = is_critical

    def deliver(self, queue: asyncio.Queue, item: Any) -> bool:
        try:
            queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            if not self._is_critical(item):
                return True  # 非关键消息可接受丢弃
        # 关键消息遇满队列——逐出一条非关键消息腾位。
        self._evict_one_non_critical(queue)
        try:
            queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            return False

    def finalize_removal(self, queue: asyncio.Queue) -> None:
        """清空 *queue* 并注入溢出信号，让消费循环终止而非永久阻塞。"""
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            queue.put_nowait(_END_OF_STREAM)
        except asyncio.QueueFull:
            pass  # 清空后不应再满

    def on_removed(self, count: int) -> None:
        pass

    def _evict_one_non_critical(self, queue: asyncio.Queue) -> None:
        """从 *queue* 中移除一条非关键消息，其余按原序放回。"""
        temp: list[Any] = []
        evicted = False
        while not queue.empty():
            try:
                msg = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not evicted and not self._is_critical(msg):
                evicted = True  # 丢弃这一条
                continue
            temp.append(msg)
        for msg in temp:
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                break


class DropSubscriber:
    """溢出策略：队列满即移除订阅者，不注入任何溢出信号。

    被移除订阅者的流不会结束（继续产出空闲心跳），断线由消费方在心跳上
    自检发现。``on_removed`` 在单次广播移除订阅者后收到移除数量（记日志用）。
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
        pass

    def on_removed(self, count: int) -> None:
        if self._on_removed is not None:
            self._on_removed(count)


SseOverflowPolicy = EvictNonCriticalAndSignal | DropSubscriber


class SseChannel:
    """一组订阅队列的注册表 + 广播扇出。

    ``subscribe`` 同步执行，消费方可在同一同步临界区内完成开场白快照与
    订阅注册，保证回放/直播无缝衔接（开场白本身不进组件）。
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
        """注册并返回一个新订阅队列。

        注册前集合为空时触发 ``on_first_subscriber``（注册完成后才调用，
        钩子内可见新订阅者——项目事件流的后台扫描以 has_subscribers 判存活）。
        """
        was_empty = not self._subscribers
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        self._subscribers.add(queue)
        if was_empty and self._on_first_subscriber is not None:
            self._on_first_subscriber()
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        """移除订阅队列；移除后集合为空则触发并等待 ``on_last_subscriber``。

        按「移除后集合为空」而非严格 1→0 转变触发：被溢出移除的订阅者事后
        退订时仍能触发收尾（溢出移除本身不触发钩子），钩子实现需幂等。
        """
        self._subscribers.discard(queue)
        if not self._subscribers and self._on_last_subscriber is not None:
            await self._on_last_subscriber()

    def unsubscribe_nowait(self, queue: asyncio.Queue) -> bool:
        """同步移除订阅队列，不触发生命周期钩子；返回移除后是否已无订阅者。

        供不可 ``await`` 的清理路径使用（如取消处理中，await 可能被二次取消
        打断致收尾半途而废），后台任务收尾由调用方自理。
        """
        self._subscribers.discard(queue)
        return not self._subscribers

    def broadcast(self, item: Any) -> None:
        """把 *item* 投递给全部订阅队列；投递失败的订阅者按溢出策略移除。"""
        stale: list[asyncio.Queue] = []
        for queue in self._subscribers:
            if not self._overflow.deliver(queue, item):
                stale.append(queue)
        for queue in stale:
            self._overflow.finalize_removal(queue)
            self._subscribers.discard(queue)
        if stale:
            self._overflow.on_removed(len(stale))

    async def iterate(self, queue: asyncio.Queue, *, idle_timeout: float) -> AsyncIterator[Any]:
        """把订阅队列变为异步迭代器：逐条产出广播项，空闲时产出 :data:`IDLE`。

        本生成器刻意不带 ``finally`` 清理——退订由消费方的 async CM ``__aexit__``
        确定性执行（见 docs/adr/0005），裸生成器的 finally 只在 GC 时跑，正是
        该设计要避免的泄漏路径。
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
