"""Forked child processes can continue using module-level engines."""

import asyncio
import os
import signal

import pytest
from sqlalchemy import text

from lib.db import async_session_factory


async def _select_one() -> int:
    async with async_session_factory() as session:
        return (await session.execute(text("SELECT 1"))).scalar_one()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="平台无 fork")
def test_forked_child_can_query_module_level_engine_after_parent_used_pool():
    assert asyncio.run(_select_one()) == 1

    pid = os.fork()
    if pid == 0:  # pragma: no cover - child process
        # Hung queries also hang event-loop shutdown; exit directly through SIGALRM instead of raising.
        signal.signal(signal.SIGALRM, lambda *_: os._exit(4))
        signal.setitimer(signal.ITIMER_REAL, 10)
        try:
            value = asyncio.run(_select_one())
        except BaseException:
            os._exit(3)
        os._exit(0 if value == 1 else 2)

    reaped = False
    try:
        _, status = os.waitpid(pid, 0)
        reaped = True
    finally:
        # Parent deadlines use pytest-timeout SIGALRM to interrupt ``waitpid`` and reap children even
        # when they never reach their own timer.
        if not reaped:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
