"""并发的两次重读装入：开始读得晚的结果不被开始读得早、却读得慢的结果覆盖。"""

from __future__ import annotations

import asyncio

from lib.catalog.reload_order import LatestReadWins


async def test_a_slow_earlier_read_does_not_overwrite_a_later_one() -> None:
    order = LatestReadWins()
    installed: list[str] = []
    release_first = asyncio.Event()

    async def slow_old_read() -> str:
        await release_first.wait()
        return "old"

    async def new_read() -> str:
        return "new"

    first = asyncio.create_task(order.reload(slow_old_read, installed.append))
    await asyncio.sleep(0)
    await order.reload(new_read, installed.append)
    release_first.set()
    await first

    assert installed == ["new"]
    # 之后再开始的重读照常装入
    await order.reload(slow_old_read, installed.append)
    assert installed == ["new", "old"]
