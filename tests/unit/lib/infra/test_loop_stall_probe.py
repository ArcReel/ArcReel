import asyncio
import logging
import re
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from lib.infra.loop_stall_probe import (
    REPEAT_WINDOW_SECONDS,
    STALL_THRESHOLD_SECONDS,
    LoopStallProbe,
    start_loop_stall_probe,
    stop_loop_stall_probe,
)

_PROBE_LOGGER = "lib.infra.loop_stall_probe"
# 用例自己驱动检查时，让守护线程在用例期间不会醒来。
_IDLE_CHECK_INTERVAL = 3600.0
_FAST_HEARTBEAT = 0.001


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _StallLog:
    def __init__(self, caplog: pytest.LogCaptureFixture) -> None:
        self._caplog = caplog

    @property
    def records(self) -> list[logging.LogRecord]:
        return [record for record in self._caplog.records if record.name == _PROBE_LOGGER]


@pytest.fixture
def stall_log(caplog: pytest.LogCaptureFixture) -> Iterator[_StallLog]:
    with caplog.at_level(logging.WARNING, logger=_PROBE_LOGGER):
        yield _StallLog(caplog)


def _hold_loop_thread(work: Callable[[], object]) -> None:
    """在事件循环线程上同步等待另一个线程跑完 ``work``：期间事件循环停滞，调用栈停在这里。"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(work).result(timeout=30)


async def _let_heartbeat_beat() -> None:
    # 心跳的定时器早于这次 sleep 到期，事件循环按到期顺序先跑心跳。
    await asyncio.sleep(_FAST_HEARTBEAT * 2)


async def test_watchdog_logs_the_event_loop_thread_stack_once_the_stall_exceeds_the_threshold(
    stall_log: _StallLog,
) -> None:
    clock = _Clock()
    logged = threading.Event()

    class _Signal(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            logged.set()

    signal = _Signal()
    logging.getLogger(_PROBE_LOGGER).addHandler(signal)
    probe = start_loop_stall_probe(heartbeat_interval=_FAST_HEARTBEAT, check_interval=0.001, clock=clock)
    try:
        await _let_heartbeat_beat()
        assert stall_log.records == []

        clock.now += STALL_THRESHOLD_SECONDS + 1
        _hold_loop_thread(lambda: logged.wait(timeout=10))
    finally:
        await stop_loop_stall_probe(probe)
        logging.getLogger(_PROBE_LOGGER).removeHandler(signal)

    assert [record.levelno for record in stall_log.records] == [logging.WARNING]
    assert "_hold_loop_thread" in stall_log.records[0].getMessage()


async def test_a_stall_shorter_than_the_threshold_is_not_logged(stall_log: _StallLog) -> None:
    clock = _Clock()
    probe = start_loop_stall_probe(heartbeat_interval=_FAST_HEARTBEAT, check_interval=_IDLE_CHECK_INTERVAL, clock=clock)
    try:
        await _let_heartbeat_beat()
        clock.now += STALL_THRESHOLD_SECONDS
        _hold_loop_thread(probe.check)
    finally:
        await stop_loop_stall_probe(probe)

    assert stall_log.records == []


async def test_one_stall_is_logged_once_however_many_checks_see_it(stall_log: _StallLog) -> None:
    clock = _Clock()
    probe = start_loop_stall_probe(heartbeat_interval=_FAST_HEARTBEAT, check_interval=_IDLE_CHECK_INTERVAL, clock=clock)
    try:
        await _let_heartbeat_beat()

        def _checks_while_the_stall_grows() -> None:
            for _ in range(5):
                clock.now += STALL_THRESHOLD_SECONDS + 1
                probe.check()

        _hold_loop_thread(_checks_while_the_stall_grows)
    finally:
        await stop_loop_stall_probe(probe)

    assert len(stall_log.records) == 1


async def test_repeated_stalls_on_the_same_stack_are_merged_into_a_count(
    stall_log: _StallLog,
) -> None:
    clock = _Clock()
    probe = start_loop_stall_probe(heartbeat_interval=_FAST_HEARTBEAT, check_interval=_IDLE_CHECK_INTERVAL, clock=clock)

    async def _stall_once() -> None:
        await _let_heartbeat_beat()
        clock.now += STALL_THRESHOLD_SECONDS + 1
        _hold_loop_thread(probe.check)

    try:
        for _ in range(3):
            await _stall_once()
        assert len(stall_log.records) == 1

        clock.now += REPEAT_WINDOW_SECONDS
        await _let_heartbeat_beat()
        probe.check()
    finally:
        await stop_loop_stall_probe(probe)

    assert len(stall_log.records) == 2
    full, summary = (record.getMessage() for record in stall_log.records)
    stack_id = re.search(r"调用栈 ([0-9a-f]{8})", full)
    assert stack_id is not None
    assert f"调用栈 {stack_id.group(1)} 上停滞了 2 次" in summary


async def test_capturing_the_stack_while_other_threads_are_busy_does_not_crash(
    stall_log: _StallLog,
) -> None:
    clock = _Clock()
    probe: LoopStallProbe = start_loop_stall_probe(
        heartbeat_interval=_FAST_HEARTBEAT, check_interval=_IDLE_CHECK_INTERVAL, clock=clock
    )
    done = threading.Event()

    def _churn(depth: int) -> int:
        # 不停压栈出栈，让帧在取栈的同时持续变化。
        return 0 if depth == 0 else 1 + _churn(depth - 1)

    def _busy() -> None:
        while not done.is_set():
            _churn(50)

    workers = [threading.Thread(target=_busy) for _ in range(4)]
    for worker in workers:
        worker.start()
    try:
        await _let_heartbeat_beat()

        def _many_stalls() -> None:
            for _ in range(200):
                clock.now += STALL_THRESHOLD_SECONDS + 1
                probe.check()

        _hold_loop_thread(_many_stalls)
    finally:
        done.set()
        for worker in workers:
            worker.join()
        await stop_loop_stall_probe(probe)

    assert len(stall_log.records) == 1
    assert "_hold_loop_thread" in stall_log.records[0].getMessage()
