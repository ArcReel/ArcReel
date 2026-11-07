"""Process-wide load ordering: reread whole tables after each committed write, applying concurrent rereads in read-start order."""

from __future__ import annotations

from collections.abc import Awaitable, Callable


class LatestReadWins:
    """After a later-started read loads, an earlier-started read cannot load after finishing.

    Two sequential commits each trigger full rereads: the earlier read may miss the later commit but finish more slowly, overwriting
    newer content until another write/restart. The later-started read necessarily includes the commit associated with the earlier read, so skipping stale results loses no content.
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
