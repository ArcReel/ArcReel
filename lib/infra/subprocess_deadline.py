"""带 deadline 运行外部子进程（ffmpeg / ffprobe 等）。

超时或所在任务被取消时：``terminate()`` → 宽限期内等待退出 → 仍未退出则 ``kill()``
→ ``wait()`` 收尸，并删除调用方声明的输出文件，避免残留进程与半成品产物。
子进程 stdin 固定接 DEVNULL，不从父进程读取输入。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_TERMINATE_GRACE_SECONDS = 2.0


class SubprocessDeadlineExceeded(TimeoutError):
    """子进程未在 deadline 内退出，已被终止。"""


class _Process(Protocol):
    @property
    def returncode(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...

    async def communicate(self) -> tuple[bytes | None, bytes | None]: ...


Spawner = Callable[..., Awaitable[_Process]]


@dataclass(frozen=True)
class SubprocessResult:
    returncode: int
    stdout: bytes


async def _reap(proc: _Process, grace: float, cleanup: list[Path]) -> None:
    try:
        await _terminate(proc, grace)
    finally:
        _remove(cleanup)


async def _terminate(proc: _Process, grace: float) -> None:
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()


async def _reap_to_completion(proc: _Process, grace: float, cleanup: list[Path]) -> None:
    """清理结束前不返回；清理期间收到的取消在清理结束后再抛出。"""
    reap = asyncio.ensure_future(_reap(proc, grace, cleanup))
    cancelled = False
    while not reap.done():
        try:
            await asyncio.shield(reap)
        except asyncio.CancelledError:
            cancelled = True
    reap.result()
    if cancelled:
        raise asyncio.CancelledError


def _remove(paths: Iterable[Path]) -> None:
    """尽力删除；删除失败只记录，不覆盖正在传播的超时或取消。"""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("未能删除子进程输出 %s", path, exc_info=True)


async def run_with_deadline(
    args: Sequence[str],
    *,
    deadline_seconds: float,
    grace: float = DEFAULT_TERMINATE_GRACE_SECONDS,
    capture_stdout: bool = False,
    cleanup_paths: Iterable[Path] = (),
    spawn: Spawner | None = None,
) -> SubprocessResult:
    """运行子进程直至退出或到达 ``deadline_seconds`` 秒。

    ``spawn`` 缺省时于调用时取 ``asyncio.create_subprocess_exec``。

    Raises:
        SubprocessDeadlineExceeded: 到达 deadline；子进程已终止并收尸，``cleanup_paths`` 已删除。
        asyncio.CancelledError: 所在任务被取消；清理同上后原样传播。
    """
    cleanup = list(cleanup_paths)
    proc = await (spawn or asyncio.create_subprocess_exec)(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if capture_stdout else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        if capture_stdout:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=deadline_seconds)
        else:
            await asyncio.wait_for(proc.wait(), timeout=deadline_seconds)
            stdout = b""
    except TimeoutError:
        await _reap_to_completion(proc, grace, cleanup)
        raise SubprocessDeadlineExceeded(f"{args[0]} 未在 {deadline_seconds}s 内退出") from None
    except asyncio.CancelledError:
        await _reap_to_completion(proc, grace, cleanup)
        raise

    assert proc.returncode is not None
    return SubprocessResult(returncode=proc.returncode, stdout=stdout or b"")
