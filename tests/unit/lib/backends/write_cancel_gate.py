"""供应商返回之后的落盘遇上取消：观测取消与写盘线程的交错。

规则见 docs/standards/backend.md「供应商返回之后的落账段不可中断」：取消落在写盘期间时，写盘线程照常
写完、取消随之消失，调用方后续的转码与产物提交继续执行。用例把写盘卡在闸门上，在写盘期间取消任务，
再放行，断言任务正常结束且后续步骤照常执行。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path


class WriteGate:
    """写盘线程的闸门：线程进入写盘即置 ``started``，等到 ``release`` 才继续。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def block(self) -> None:
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError("写盘闸门未放行")


class GatedWritePath(type(Path())):
    """``write_bytes`` 被闸门挡住的 ``output_path``。

    作为请求的输入对象注入，不 patch 生产符号。
    """

    def attach(self, gate: WriteGate) -> GatedWritePath:
        self._gate = gate
        return self

    def write_bytes(self, data) -> int:
        self._gate.block()
        return super().write_bytes(data)

    def with_name(self, name: str) -> GatedWritePath:
        return super().with_name(name).attach(self._gate)


async def cancel_during_write(operation: Callable[[], Awaitable[object]], gate: WriteGate) -> list[str]:
    """运行 ``operation``，在它的写盘线程卡住期间取消任务，放行后返回后续步骤的执行记录。

    ``operation`` 之后追加一个“后续步骤”标记，代表调用方的转码与产物提交。取消若穿透了落盘段，
    任务以 ``CancelledError`` 结束，这里随之抛出，后续步骤记录也不会出现。
    """
    steps: list[str] = []

    async def settle_then_commit() -> None:
        await operation()
        steps.append("follow-up")

    task = asyncio.create_task(settle_then_commit())
    try:
        assert await asyncio.to_thread(gate.started.wait, 5), "写盘线程未启动"
        task.cancel()
        # 线程往返把控制权交还事件循环，可中断的实现会在此结束
        await asyncio.to_thread(lambda: None)
        assert not task.done(), "取消不得在写盘结算前结束任务"
    finally:
        gate.release.set()
    await asyncio.wait_for(task, 5)
    return steps
