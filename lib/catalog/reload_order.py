"""进程级装入的先后：每次写入提交后重读整表再装入目录查询，并发的两次重读按开始读的先后生效。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable


class LatestReadWins:
    """开始读得晚的一次已经装入时，开始读得早的一次读完后不再装入。

    两个写入先后提交、各自重读整表：先开始的那次读可能读不到后一次提交，却因为读得慢而后装入，把较新的内容
    换回旧的，直到下一次写入或重启。开始读得晚的那次一定读到了更早开始的那次所在的提交，跳过旧结果不丢内容。
    """

    def __init__(self) -> None:
        self._started = 0
        self._installed = -1

    async def reload[T](self, read: Callable[[], Awaitable[T]], install: Callable[[T], None]) -> None:
        ticket = self._started
        self._started += 1
        loaded = await read()
        if ticket < self._installed:
            return
        self._installed = ticket
        install(loaded)
