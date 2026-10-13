"""事件循环卡顿探针：循环被卡住超过阈值时，把所有线程的调用栈转储到 stderr。

心跳协程每秒重置一次 faulthandler 的延时转储。事件循环正常调度时转储永远等不到触发；
循环线程被同步代码占住、心跳来不及重置时，faulthandler 的看门狗线程在到期后写出调用栈。
循环可能在两次心跳之间的任一时刻停住，延时因此取阈值加一个心跳间隔：停滞不足阈值时不会转储，
停滞超过阈值后至多再过一个心跳间隔写出。只用标准库，心跳本身不含阻塞调用。
"""

from __future__ import annotations

import asyncio
import contextlib
import faulthandler
import logging
import sys

logger = logging.getLogger(__name__)

# 心跳间隔（秒）。
HEARTBEAT_INTERVAL_SECONDS = 1.0
# 事件循环停滞多久后转储调用栈（秒）。
STALL_DUMP_TIMEOUT_SECONDS = 2.0
_DUMP_DELAY_SECONDS = STALL_DUMP_TIMEOUT_SECONDS + HEARTBEAT_INTERVAL_SECONDS


async def _heartbeat(stderr_fd: int) -> None:
    while True:
        faulthandler.dump_traceback_later(_DUMP_DELAY_SECONDS, repeat=False, file=stderr_fd)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


def start_loop_stall_probe() -> asyncio.Task[None] | None:
    """在当前事件循环上启动心跳协程；返回的 task 交给 `stop_loop_stall_probe` 收尾。

    转储写进进程的原始 stderr（``sys.__stderr__``）的文件描述符，它在进程存续期间一直打开。
    ``sys.stderr`` 可能被临时替换（测试捕获、宿主重定向），替换对象的描述符随时会关闭并被复用，
    延时转储会写进不相干的文件。原始 stderr 没有可用的文件描述符（无控制台的宿主）时不启动探针，
    返回 None。
    """
    stderr_fd = _process_stderr_fd()
    if stderr_fd is None:
        logger.warning("事件循环卡顿探针未启动：stderr 没有可用的文件描述符")
        return None
    return asyncio.get_running_loop().create_task(_heartbeat(stderr_fd), name="loop-stall-probe")


def _process_stderr_fd() -> int | None:
    if sys.__stderr__ is None:
        return None
    try:
        return sys.__stderr__.fileno()
    except (ValueError, OSError):
        return None


async def stop_loop_stall_probe(task: asyncio.Task[None] | None) -> None:
    """取消心跳协程并撤销尚未触发的延时转储；探针未启动（``task`` 为 None）时什么也不做。

    先撤销转储再等心跳退出：关停本身被取消时，``await`` 处会再次抛出取消，撤销不能排在它后面。
    """
    if task is None:
        return
    task.cancel()
    faulthandler.cancel_dump_traceback_later()
    with contextlib.suppress(asyncio.CancelledError):
        await task
