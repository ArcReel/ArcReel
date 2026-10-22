"""聚合读取的请求合流：同一键同时最多 1 份在算、1 份等待。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


class RequestCoalescer:
    """把同一键上的并发请求合进至多两份计算。

    没有计算在跑时立即开算；有计算在跑时，新请求全部挂到唯一的等待位上，共享下一次计算。
    等待位要等当前计算结束才开始，并在开始时才读取输入，所以任何请求拿到的结果都不早于它
    到达的时刻。计算出错时共享者收到同一个异常，键随之空出，下一次请求重新计算。

    进程内、不缓存跨请求的结果。键必须包含所有会影响响应内容的请求维度（至少是语言）。
    计算放在独立任务里、经 ``asyncio.shield`` 等待：某个请求被取消（客户端断开）只影响
    它自己，共享的计算和其他等待者照常拿到结果。
    """

    def __init__(self) -> None:
        self._running: dict[Hashable, asyncio.Task[Any]] = {}
        self._waiting: dict[Hashable, asyncio.Task[Any]] = {}

    async def run[T](self, key: Hashable, compute: Callable[[], Awaitable[T]]) -> T:
        task = self._waiting.get(key)
        if task is None:
            previous = self._running.get(key)
            task = asyncio.create_task(self._compute(key, previous, compute))
            # 请求可能已全部断开而无人取走异常；取走一次以免任务回收时记 "never retrieved"。
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
                # 等待位转正：此后到达的请求另开新的等待位，读取更新的输入。
                self._running[key] = self._waiting.pop(key)
            return await compute()
        finally:
            for slots in (self._running, self._waiting):
                if slots.get(key) is me:
                    del slots[key]
