"""Hang diagnosis: dump all thread stacks when a process makes no test progress for an extended period.

Loaded by root conftest ``pytest_plugins``; enabled only when ``ARCREEL_TEST_HANG_DUMP_DIR`` names
a directory (CI backend-test jobs), leaving local runs unaffected. The xdist controller and each worker
write separate files with worker id and pid, distinguishing replacement workers from crashed originals:

- ``<id>-<pid>.progress``: executing processes record each starting case. After a worker is killed outright,
  the last line identifies its active case.
- ``<id>-<pid>.stacks``: after ``IDLE_DUMP_SECONDS`` without progress, record the current phase and dump
  all thread stacks; repeat at the same interval while idle. Before ``os._exit``, likewise record the exit
  code and all thread stacks.
- ``<id>-<pid>.<信号名>``: all thread stacks upon a ``CRASH_SIGNALS`` signal, then the original handling
  continues (SIGTERM still terminates; SIGINT still raises KeyboardInterrupt).
- The controller's ``.progress`` also records each crashed worker and its exit code or terminating signal.

pytest-timeout covers only individual setup/call/teardown phases; its signal method also waits for main-thread
bytecode. It misses session teardown and worker exits waiting for non-daemon threads. This independent daemon
watchdog covers those phases too. Avoid faulthandler ``dump_traceback_later``: its single process-wide timer
can be reset or canceled by any other caller, losing the dump.
"""

from __future__ import annotations

import faulthandler
import os
import signal
import subprocess
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, NoReturn, TextIO

import pytest

DUMP_DIR_ENV = "ARCREEL_TEST_HANG_DUMP_DIR"
# Override the no-progress threshold (seconds) so this plugin's contract tests can wait less.
IDLE_SECONDS_ENV = "ARCREEL_TEST_HANG_IDLE_SECONDS"
# Individual cases time out after 120s (pyproject timeout); allow margin and dump only if timeout also fails to finish.
IDLE_DUMP_SECONDS = 180.0
# Dump on SIGTERM/SIGHUP, which otherwise terminate silently, and SIGINT from execnet loss or CI cancellation.
# Fatal signals such as SIGSEGV belong to pytest's faulthandler plugin, which writes stacks to stderr; it sets up an
# alternate signal stack only for its enabling thread. Stack overflow elsewhere prevents handlers from running and kills silently.
CRASH_SIGNALS = ("SIGTERM", "SIGHUP", "SIGINT")
# Maximum seconds to wait for a crashed worker's process exit and retrieve its status.
EXIT_STATUS_WAIT_SECONDS = 5.0

_real_os_exit = os._exit

# One watchdog per process; controller report hooks cannot access config, hence module-level storage.
_watchdog: HangWatchdog | None = None


class HangWatchdog:
    """Record progress; dump all thread stacks to ``stacks`` after ``idle_seconds`` without progress."""

    def __init__(self, progress: TextIO, stacks: TextIO, idle_seconds: float) -> None:
        self._progress = progress
        self._stacks = stacks
        self._idle_seconds = idle_seconds
        self.pid = os.getpid()
        self._phase = "startup"
        self._last_progress = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="hang-watchdog", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def mark(self, phase: str, *, record: bool = False) -> None:
        """Record the current phase and restart timing; also append to the progress file when ``record`` is true."""
        self._phase = phase
        self._last_progress = time.monotonic()
        if record:
            self.note(phase)

    def note(self, line: str) -> None:
        """Append a progress-file line without changing the current phase."""
        self._progress.write(f"{_timestamp()} {line}\n")

    def dump(self, reason: str) -> None:
        """Write the reason, current phase, and all thread stacks to ``stacks``."""
        self._stacks.write(f"===== {_timestamp()} {reason}，当前阶段：{self._phase}\n")
        self._stacks.flush()
        faulthandler.dump_traceback(file=self._stacks, all_threads=True)

    def _run(self) -> None:
        while True:
            time.sleep(min(self._idle_seconds / 4, 5.0))
            idle = time.monotonic() - self._last_progress
            if idle < self._idle_seconds:
                continue
            self.dump(f"已 {idle:.0f}s 无进展")
            self._last_progress = time.monotonic()


def _timestamp() -> str:
    return time.strftime("%H:%M:%S", time.gmtime())


def _traced_os_exit(status: int) -> NoReturn:
    # os._exit skips finally/atexit and immediately disappears; first record its caller and call path.
    # Test-forked children still finish through os._exit without dumping.
    # Exit normally even if dumping fails (e.g. a full disk).
    try:
        if _watchdog is not None and _watchdog.pid == os.getpid():
            _watchdog.dump(f"调用 os._exit({status})")
    finally:
        _real_os_exit(status)


def _dump_stacks_on_signals(directory: Path, stem: str) -> None:
    if not hasattr(faulthandler, "register"):  # Unavailable on Windows
        return
    for name in CRASH_SIGNALS:
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        # faulthandler writes directly to fd in signal handlers, allowing dumps even when the main thread stalls in C; it holds
        # a reference to the file object, preventing collection. chain=True preserves original signal handling.
        file = (directory / f"{stem}.{name}").open("a", encoding="utf-8")
        faulthandler.register(signum, file=file, all_threads=True, chain=True)


def _describe_exit(node: Any) -> str:
    """Retrieve a crashed worker's process exit status, explaining when unavailable."""
    # execnet does not expose the popen gateway subprocess; inspect internals and fall back to an explanation if they change.
    popen = getattr(getattr(getattr(node, "gateway", None), "_io", None), "popen", None)
    if not isinstance(popen, subprocess.Popen):
        return "取不到进程退出状态"
    try:
        returncode = popen.wait(timeout=EXIT_STATUS_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        return f"进程 {EXIT_STATUS_WAIT_SECONDS:.0f}s 内未退出"
    if returncode < 0:
        return f"进程被信号 {signal.Signals(-returncode).name} 终止"
    return f"进程退出码 {returncode}"


def pytest_configure(config: pytest.Config) -> None:
    global _watchdog
    dump_dir = os.environ.get(DUMP_DIR_ENV, "").strip()
    if not dump_dir:
        return
    directory = Path(dump_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{os.environ.get('PYTEST_XDIST_WORKER', 'controller')}-{os.getpid()}"
    # Line buffering: every written line has reached the kernel even if the process is killed outright.
    progress = (directory / f"{stem}.progress").open("a", buffering=1, encoding="utf-8")
    stacks = (directory / f"{stem}.stacks").open("a", buffering=1, encoding="utf-8")
    idle_seconds = float(os.environ.get(IDLE_SECONDS_ENV) or IDLE_DUMP_SECONDS)
    _watchdog = HangWatchdog(progress, stacks, idle_seconds)
    _watchdog.mark("configure")
    _watchdog.start()
    os._exit = _traced_os_exit
    _dump_stacks_on_signals(directory, stem)


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
    # The xdist controller executes no cases and counts progress from worker reports.
    if _watchdog is not None:
        _watchdog.mark(f"report {report.nodeid} {report.when}")


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: object | None) -> None:
    # Only the xdist controller has this hook; empty error means normal shutdown.
    if _watchdog is not None and error is not None:
        worker_id = getattr(getattr(node, "gateway", None), "id", "?")
        _watchdog.note(f"worker {worker_id} 宕机（{error}），{_describe_exit(node)}")


def pytest_sessionfinish() -> None:
    if _watchdog is not None:
        _watchdog.mark("sessionfinish", record=True)


def pytest_unconfigure() -> None:
    # Keep the watchdog running: after unconfigure, non-daemon waits and atexit can still hang and require dumps.
    if _watchdog is not None:
        _watchdog.mark("unconfigure，等待进程退出", record=True)
