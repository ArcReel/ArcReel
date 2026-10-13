import asyncio
import faulthandler
import io
import logging
import sys
from pathlib import Path

import pytest

from lib.infra.loop_stall_probe import (
    HEARTBEAT_INTERVAL_SECONDS,
    STALL_DUMP_TIMEOUT_SECONDS,
    start_loop_stall_probe,
    stop_loop_stall_probe,
)


async def test_probe_is_not_started_when_process_stderr_has_no_file_descriptor(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(sys, "__stderr__", None)

    with caplog.at_level(logging.WARNING, logger="lib.infra.loop_stall_probe"):
        probe = start_loop_stall_probe()

    assert probe is None
    assert [rec.levelno for rec in caplog.records] == [logging.WARNING]
    await stop_loop_stall_probe(probe)


async def test_probe_does_not_depend_on_the_replaceable_sys_stderr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # sys.stderr 被换成没有文件描述符的对象（测试捕获、宿主重定向）时，探针照常运行，转储写进原始 stderr。
    with (tmp_path / "stderr.log").open("w") as process_stderr:
        monkeypatch.setattr(sys, "__stderr__", process_stderr)
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        probe = start_loop_stall_probe()
        assert probe is not None
        await asyncio.sleep(0)

        assert not probe.done()
        await stop_loop_stall_probe(probe)
        assert probe.cancelled()


async def test_dump_fires_only_after_the_loop_has_stalled_for_the_full_threshold(monkeypatch: pytest.MonkeyPatch):
    armed: list[float] = []
    monkeypatch.setattr(faulthandler, "dump_traceback_later", lambda timeout, **_: armed.append(timeout))
    probe = start_loop_stall_probe()
    assert probe is not None
    await asyncio.sleep(0)
    await stop_loop_stall_probe(probe)

    # 循环可能在上次心跳后、下次心跳前的任一时刻停住，最坏情况已经过去整整一个心跳间隔。
    assert armed
    assert all(timeout - HEARTBEAT_INTERVAL_SECONDS >= STALL_DUMP_TIMEOUT_SECONDS for timeout in armed)
