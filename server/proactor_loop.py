"""uvicorn 自定义事件循环工厂。

Windows 上 uvicorn 在 --reload / 多 worker 模式下会选用 SelectorEventLoop
（见 uvicorn.loops.asyncio.asyncio_loop_factory），该事件循环不支持
asyncio.create_subprocess_exec，Claude Agent SDK 启动 claude.exe 子进程时直接
抛 NotImplementedError。这里在 Windows 上强制 ProactorEventLoop，其他平台沿用
uvicorn auto 行为（有 uvloop 用 uvloop）。

uvicorn ``--loop`` 的自定义路径约定：工厂函数直接交给 asyncio.run，必须返回
事件循环实例（不是类）。
"""

from __future__ import annotations

import asyncio
import sys


def proactor_loop_factory() -> asyncio.AbstractEventLoop:
    """返回事件循环实例，供 uvicorn ``--loop`` 使用。"""
    if sys.platform == "win32":
        return asyncio.ProactorEventLoop()
    from uvicorn.loops.auto import auto_loop_factory

    return auto_loop_factory(use_subprocess=False)()
