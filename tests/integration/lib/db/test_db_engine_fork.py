"""fork 出的子进程能继续使用模块级 engine。"""

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
    if pid == 0:  # pragma: no cover - 子进程
        # 挂起的查询连事件循环的收尾也一并挂住，故用 SIGALRM 直接退出而非抛异常。
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
        # 父进程侧的期限由 pytest-timeout 的 SIGALRM 打断 ``waitpid`` 提供，子进程连
        # 自己的定时器都没跑到时也在这里被回收。
        if not reaped:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
