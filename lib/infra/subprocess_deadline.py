"""Run external subprocesses (ffmpeg, etc.) with a deadline.

On timeout or task cancellation: ``terminate()`` → wait for exit during the grace period
→ ``kill()`` if still running → ``wait()`` to reap; delete caller-declared output files
so neither orphan processes nor partial outputs remain.
Subprocess stdin always uses DEVNULL and never reads input from the parent.
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
    """The subprocess did not exit within its deadline and has been terminated."""


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
    stderr: bytes = b""


async def _reap(proc: _Process, grace: float, cleanup: list[Path]) -> None:
    try:
        await _terminate(proc, grace)
    finally:
        # Run only through _reap_to_completion under shield; cancellation cannot interrupt this deletion
        await asyncio.to_thread(_remove, cleanup)


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
    """Do not return until cleanup finishes; re-raise cancellation received during cleanup afterward."""
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
    """Delete best effort; log failures without masking the propagating timeout or cancellation."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)  # hard-delete: temp -- output left by a subprocess killed after timeout
        except OSError:
            logger.warning("未能删除子进程输出 %s", path, exc_info=True)


async def run_with_deadline(
    args: Sequence[str],
    *,
    deadline_seconds: float,
    grace: float = DEFAULT_TERMINATE_GRACE_SECONDS,
    capture_stdout: bool = False,
    capture_stderr: bool = False,
    cleanup_paths: Iterable[Path] = (),
    spawn: Spawner | None = None,
    cwd: Path | None = None,
) -> SubprocessResult:
    """Run a subprocess until it exits or ``deadline_seconds`` elapses.

    Without ``spawn``, resolve ``asyncio.create_subprocess_exec`` at call time; ``cwd`` is
    the subprocess working directory, inherited from the current process by default.

    Raises:
        SubprocessDeadlineExceeded: Deadline reached; the subprocess was terminated and reaped, and ``cleanup_paths`` deleted.
        asyncio.CancelledError: The task was cancelled; propagate unchanged after the same cleanup.
    """
    cleanup = list(cleanup_paths)
    proc = await (spawn or asyncio.create_subprocess_exec)(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if capture_stdout else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE if capture_stderr else asyncio.subprocess.DEVNULL,
        cwd=cwd,
    )
    try:
        if capture_stdout or capture_stderr:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=deadline_seconds)
        else:
            await asyncio.wait_for(proc.wait(), timeout=deadline_seconds)
            stdout = stderr = b""
    except TimeoutError:
        await _reap_to_completion(proc, grace, cleanup)
        raise SubprocessDeadlineExceeded(f"{args[0]} 未在 {deadline_seconds}s 内退出") from None
    except asyncio.CancelledError:
        await _reap_to_completion(proc, grace, cleanup)
        raise

    assert proc.returncode is not None
    return SubprocessResult(returncode=proc.returncode, stdout=stdout or b"", stderr=stderr or b"")
