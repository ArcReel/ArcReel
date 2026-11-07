"""Restore cancellation absorbed by a dependency inside the task itself.

Within an anyio task group or cancellation scope (httpx enters ``anyio.connect_tcp``
through httpcore), cancellation of that scope in the same scheduling turn can
merge external ``Task.cancel()`` into anyio cancellation. Scope exit swallows it
entirely, leaving the task running with a nonzero ``cancelling()`` count.

Do not repeatedly call ``cancel()`` from outside: another cancellation interrupts cleanup
inside ``asyncio.shield`` (SQLAlchemy session close and connection return), allowing
the waiter to return before cleanup finishes. The task calls
``raise_if_cancel_absorbed`` at a checkpoint after the dependency call; the waiter
cancels once and waits until the task ends.
"""

from __future__ import annotations

import asyncio


def raise_if_cancel_absorbed() -> None:
    """Raise ``CancelledError`` when the current task has an unconsumed cancellation request.

    Call only at the task's own checkpoints, with no cancellation propagating here:
    a nonzero ``cancelling()`` means a lower layer absorbed the request.
    """
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


__all__ = ["raise_if_cancel_absorbed"]
