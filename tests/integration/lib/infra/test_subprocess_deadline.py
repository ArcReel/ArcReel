"""带 deadline 运行真实子进程：超时后子进程被收尸。"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from lib.infra.subprocess_deadline import SubprocessDeadlineExceeded, run_with_deadline


@pytest.mark.skipif(shutil.which("sleep") is None, reason="sleep not available")
async def test_real_process_is_reaped_after_deadline():
    spawned: list[asyncio.subprocess.Process] = []

    async def spawn(*args, **kwargs):
        proc = await asyncio.create_subprocess_exec(*args, **kwargs)
        spawned.append(proc)
        return proc

    with pytest.raises(SubprocessDeadlineExceeded):
        await run_with_deadline(["sleep", "3600"], deadline_seconds=0, spawn=spawn)

    (proc,) = spawned
    assert proc.returncode is not None
