"""Concurrent reloads: a later-started result must not be overwritten by an earlier but slower read."""

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
    # Subsequent reloads still apply normally.
    await order.reload(slow_old_read, installed.append)
    assert installed == ["new", "old"]
