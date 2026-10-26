"""挂起诊断：进程长时间没有用例进展时，把全部线程的调用栈写进转储目录。

由根 conftest 的 ``pytest_plugins`` 加载，只在环境变量 ``ARCREEL_TEST_HANG_DUMP_DIR``
指定目录时启用（CI 的后端测试 job），本地运行不受影响。xdist 的 controller 与每个 worker
各自写一组文件，文件名带 worker 编号与 pid，崩溃后补上的 worker 与原 worker 区分开：

- ``<id>-<pid>.progress``：执行用例的进程逐条记下开始执行的用例。worker 被直接杀掉时，
  最后一行就是它正在跑的用例。
- ``<id>-<pid>.stacks``：进程在 ``IDLE_DUMP_SECONDS`` 内没有任何进展时，记下当前阶段并转储
  全部线程的调用栈；此后仍无进展则每隔同样时长再转储一次。

pytest-timeout 只管单个用例的 setup/call/teardown，而且 signal 法要等主线程回到字节码才能
生效；会话收尾、worker 退出时等待非 daemon 线程这类卡点它看不到。这里的看门狗是独立的
daemon 线程，这些阶段都覆盖。不借用 faulthandler 的 ``dump_traceback_later``：它在进程内只有
一个定时器，``lib/infra/loop_stall_probe`` 在应用 lifespan 里会重置并撤销它。
"""

from __future__ import annotations

import faulthandler
import os
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import TextIO

import pytest

DUMP_DIR_ENV = "ARCREEL_TEST_HANG_DUMP_DIR"
# 覆盖无进展阈值（秒），供本插件自身的契约测试缩短等待。
IDLE_SECONDS_ENV = "ARCREEL_TEST_HANG_IDLE_SECONDS"
# 单个用例的超时是 120s（pyproject 的 timeout），这里留出余量，只在用例超时也没能收场时转储。
IDLE_DUMP_SECONDS = 180.0

# 每个进程只有一个看门狗；controller 的报告钩子拿不到 config，故放在模块级。
_watchdog: HangWatchdog | None = None


class HangWatchdog:
    """记录进展；``idle_seconds`` 内无进展时把全部线程的调用栈写进 ``stacks``。"""

    def __init__(self, progress: TextIO, stacks: TextIO, idle_seconds: float) -> None:
        self._progress = progress
        self._stacks = stacks
        self._idle_seconds = idle_seconds
        self._phase = "startup"
        self._last_progress = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="hang-watchdog", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def mark(self, phase: str, *, record: bool = False) -> None:
        """记下当前阶段并重新计时；``record`` 为真时同时追加到进展文件。"""
        self._phase = phase
        self._last_progress = time.monotonic()
        if record:
            self._progress.write(f"{_timestamp()} {phase}\n")

    def _run(self) -> None:
        while True:
            time.sleep(min(self._idle_seconds / 4, 5.0))
            idle = time.monotonic() - self._last_progress
            if idle < self._idle_seconds:
                continue
            self._stacks.write(f"===== {_timestamp()} 已 {idle:.0f}s 无进展，当前阶段：{self._phase}\n")
            self._stacks.flush()
            faulthandler.dump_traceback(file=self._stacks, all_threads=True)
            self._last_progress = time.monotonic()


def _timestamp() -> str:
    return time.strftime("%H:%M:%S", time.gmtime())


def pytest_configure(config: pytest.Config) -> None:
    global _watchdog
    dump_dir = os.environ.get(DUMP_DIR_ENV, "").strip()
    if not dump_dir:
        return
    directory = Path(dump_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{os.environ.get('PYTEST_XDIST_WORKER', 'controller')}-{os.getpid()}"
    # 行缓冲：进程被直接杀掉时，已写下的每一行都已落到内核。
    progress = (directory / f"{stem}.progress").open("a", buffering=1, encoding="utf-8")
    stacks = (directory / f"{stem}.stacks").open("a", buffering=1, encoding="utf-8")
    idle_seconds = float(os.environ.get(IDLE_SECONDS_ENV) or IDLE_DUMP_SECONDS)
    _watchdog = HangWatchdog(progress, stacks, idle_seconds)
    _watchdog.mark("configure")
    _watchdog.start()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item) -> Generator[None, object, object]:
    if _watchdog is not None:
        _watchdog.mark(f"run {item.nodeid}", record=True)
    try:
        return (yield)
    finally:
        if _watchdog is not None:
            _watchdog.mark(f"after {item.nodeid}")


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    # xdist 的 controller 不执行用例，靠 worker 回传的报告计进展。
    if _watchdog is not None:
        _watchdog.mark(f"report {report.nodeid} {report.when}")


def pytest_sessionfinish() -> None:
    if _watchdog is not None:
        _watchdog.mark("sessionfinish", record=True)


def pytest_unconfigure() -> None:
    # 不停看门狗：unconfigure 之后进程还要等非 daemon 线程、跑 atexit，卡在这里同样要转储。
    if _watchdog is not None:
        _watchdog.mark("unconfigure，等待进程退出", record=True)
