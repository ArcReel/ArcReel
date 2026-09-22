"""带 deadline 运行子进程：超时与取消时的终止升级与产物清理。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lib.infra.subprocess_deadline import SubprocessDeadlineExceeded, run_with_deadline
from tests.fakes import HangingProcess


class _ExitingProcess:
    def __init__(self, returncode: int, stdout: bytes) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self.signals: list[str] = []

    def terminate(self) -> None:
        self.signals.append("terminate")

    def kill(self) -> None:
        self.signals.append("kill")

    async def wait(self) -> int:
        return self.returncode

    async def communicate(self) -> tuple[bytes, None]:
        return self._stdout, None


class _Spawner:
    def __init__(self, proc) -> None:
        self._proc = proc
        self.calls: list[tuple[tuple, dict]] = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._proc


async def test_returns_exit_code_and_stdout_when_process_finishes(tmp_path: Path):
    proc = _ExitingProcess(0, b"42\n")
    spawn = _Spawner(proc)

    result = await run_with_deadline(["ffprobe", "x"], deadline_seconds=5, capture_stdout=True, spawn=spawn)

    assert result.returncode == 0
    assert result.stdout == b"42\n"
    assert proc.signals == []
    _, kwargs = spawn.calls[0]
    assert kwargs["stdin"] == asyncio.subprocess.DEVNULL


@pytest.mark.parametrize(
    ("honors_terminate", "grace", "expected_signals"),
    [(True, 3600, ["terminate"]), (False, 0, ["terminate", "kill"])],
)
async def test_deadline_terminates_then_kills_and_removes_outputs(
    tmp_path: Path, honors_terminate: bool, grace: float, expected_signals: list[str]
):
    proc = HangingProcess(honors_terminate=honors_terminate)
    partial = tmp_path / "partial.jpg"
    partial.write_bytes(b"half")

    with pytest.raises(SubprocessDeadlineExceeded):
        await run_with_deadline(
            ["ffmpeg"], deadline_seconds=0, grace=grace, cleanup_paths=[partial], spawn=_Spawner(proc)
        )

    assert proc.signals == expected_signals
    assert proc.returncode is not None
    assert not partial.exists()


async def test_cancellation_during_wait_reaps_process_and_removes_outputs(tmp_path: Path):
    proc = HangingProcess(honors_terminate=False)
    partial = tmp_path / "partial.png"
    partial.write_bytes(b"half")

    task = asyncio.create_task(
        run_with_deadline(["ffmpeg"], deadline_seconds=3600, grace=0, cleanup_paths=[partial], spawn=_Spawner(proc))
    )
    await proc.waiting.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.signals == ["terminate", "kill"]
    assert proc.returncode is not None
    assert not partial.exists()


async def test_cancellation_during_communicate_reaps_process(tmp_path: Path):
    proc = HangingProcess(honors_terminate=True)

    task = asyncio.create_task(
        run_with_deadline(["ffprobe"], deadline_seconds=3600, grace=3600, capture_stdout=True, spawn=_Spawner(proc))
    )
    await proc.waiting.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.signals == ["terminate"]
    assert proc.returncode is not None


async def test_repeated_cancellation_waits_for_cleanup_to_finish(tmp_path: Path):
    proc = HangingProcess(honors_terminate=False)
    partial = tmp_path / "partial.png"
    partial.write_bytes(b"half")

    task = asyncio.create_task(
        run_with_deadline(["ffmpeg"], deadline_seconds=3600, grace=3600, cleanup_paths=[partial], spawn=_Spawner(proc))
    )
    await proc.waiting.wait()
    task.cancel()
    await proc.terminate_requested.wait()
    task.cancel()
    await asyncio.sleep(0)  # 取消经一次回调送达任务

    assert not task.done()
    assert partial.exists()

    proc.kill()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not partial.exists()


async def test_deadline_is_reported_even_if_output_cannot_be_removed(tmp_path: Path):
    undeletable = tmp_path / "out"
    undeletable.mkdir()

    with pytest.raises(SubprocessDeadlineExceeded):
        await run_with_deadline(
            ["ffmpeg"],
            deadline_seconds=0,
            grace=0,
            cleanup_paths=[undeletable],
            spawn=_Spawner(HangingProcess(honors_terminate=False)),
        )
