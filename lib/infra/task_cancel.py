"""让被依赖库吞掉的取消在任务自己的代码里重新生效。

任务在 anyio 的任务组或取消域里时（httpx 建立连接时经 httpcore 进入 ``anyio.connect_tcp`` 的任务组），
anyio 若恰在同一轮调度里取消自己的域，外部的 ``Task.cancel()`` 会与它合并成一个 anyio 取消，在域出口被整个吞掉：
任务继续运行，``cancelling()`` 停在非零。

不从外部反复 ``cancel()`` 补发：重投的取消会打断任务正在 ``asyncio.shield`` 里做的收尾（SQLAlchemy 会话的关闭与
连接归还），等待方返回时收尾还没做完。由任务在依赖库调用之后的检查点调用 :func:`raise_if_cancel_absorbed`，
等待方只取消一次、一直等到任务结束。
"""

from __future__ import annotations

import asyncio


def raise_if_cancel_absorbed() -> None:
    """当前任务有未生效的取消请求时抛出 ``CancelledError``。

    只在任务自己的检查点调用，此处没有正在传播的取消：``cancelling()`` 非零说明取消请求被下层吞掉了。
    """
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


__all__ = ["raise_if_cancel_absorbed"]
