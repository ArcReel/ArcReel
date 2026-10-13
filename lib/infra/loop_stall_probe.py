"""事件循环卡顿探针：循环被卡住超过阈值时，把所有线程的调用栈转储到 stderr。

心跳协程每秒重置一次 faulthandler 的延时转储。事件循环正常调度时转储永远等不到触发；
循环线程被同步代码占住、心跳来不及重置时，faulthandler 的看门狗线程在阈值到期后写出调用栈。
只用标准库，心跳本身不含阻塞调用。
"""

from __future__ import annotations

import asyncio
import contextlib
import faulthandler
import sys

# 心跳间隔（秒）。
HEARTBEAT_INTERVAL_SECONDS = 1.0
# 事件循环停滞多久后转储调用栈（秒）；上线实测后按需调整。
STALL_DUMP_TIMEOUT_SECONDS = 2.0


async def _heartbeat() -> None:
    while True:
        faulthandler.dump_traceback_later(STALL_DUMP_TIMEOUT_SECONDS, repeat=False, file=sys.stderr)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


def start_loop_stall_probe() -> asyncio.Task[None]:
    """在当前事件循环上启动心跳协程；返回的 task 交给 `stop_loop_stall_probe` 收尾。"""
    return asyncio.get_running_loop().create_task(_heartbeat(), name="loop-stall-probe")


async def stop_loop_stall_probe(task: asyncio.Task[None]) -> None:
    """取消心跳协程并撤销尚未触发的延时转储。"""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    faulthandler.cancel_dump_traceback_later()
