"""挂起诊断插件（`tests/hang_watchdog.py`）的端到端契约。

每条用例在临时目录里另起一个 pytest 会话，只加载该插件。契约：

- 会话结束后进程迟迟不退出（被非 daemon 线程挡住）时，转储里有当前阶段与挡路线程的调用栈。
- worker 被直接杀掉时，进展文件的最后一条是它正在执行的用例。
- 进程调用 ``os._exit`` 时，转储里有退出码与调用它的栈；收到 SIGTERM 时，信号文件里有当时的栈，
  进程照常被结束。用例 fork 出的子进程用 ``os._exit`` 收尾不算。
- xdist 的 worker 宕掉时，controller 的进展文件记下它和终止它的信号。
- 未设转储目录时插件不写任何文件。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(
    root: Path, source: str, *, dump_dir: Path | None, extra_args: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    (root / "test_probe.py").write_text(textwrap.dedent(source), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT), "ARCREEL_TEST_HANG_IDLE_SECONDS": "0.4"}
    env.pop("ARCREEL_TEST_HANG_DUMP_DIR", None)
    env.pop("PYTEST_XDIST_WORKER", None)
    if dump_dir is not None:
        env["ARCREEL_TEST_HANG_DUMP_DIR"] = str(dump_dir)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.hang_watchdog",
            "-p",
            "no:cacheprovider",
            "-q",
            *extra_args,
            "test_probe.py",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _single(dump_dir: Path, suffix: str) -> str:
    (path,) = dump_dir.glob(f"controller-*.{suffix}")
    return path.read_text(encoding="utf-8")


def test_dumps_thread_blocking_process_exit(tmp_path: Path) -> None:
    dump_dir = tmp_path / "dump"
    result = _run(
        tmp_path,
        """
        import os
        import threading
        import time
        from pathlib import Path

        def linger_after_session():
            # 握手：一直挡住进程退出，直到转储里出现本线程的栈；30 秒上限只防看门狗失效时挂住用例。
            dump_dir = Path(os.environ["ARCREEL_TEST_HANG_DUMP_DIR"])
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                dumped = "".join(path.read_text(encoding="utf-8") for path in dump_dir.glob("*.stacks"))
                if "linger_after_session" in dumped:
                    return
                time.sleep(0.05)

        def test_leaks_non_daemon_thread():
            threading.Thread(target=linger_after_session).start()
        """,
        dump_dir=dump_dir,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    stacks = _single(dump_dir, "stacks")
    assert "当前阶段：unconfigure" in stacks
    assert "linger_after_session" in stacks


def test_records_test_and_stack_when_process_calls_os_exit(tmp_path: Path) -> None:
    dump_dir = tmp_path / "dump"
    result = _run(
        tmp_path,
        """
        import os

        def test_first():
            pass

        def test_killed():
            os._exit(9)
        """,
        dump_dir=dump_dir,
    )

    assert result.returncode == 9
    assert _single(dump_dir, "progress").splitlines()[-1].endswith("run test_probe.py::test_killed")
    stacks = _single(dump_dir, "stacks")
    assert "调用 os._exit(9)" in stacks
    assert "test_killed" in stacks


def test_forked_child_exiting_via_os_exit_leaves_no_dump(tmp_path: Path) -> None:
    dump_dir = tmp_path / "dump"
    result = _run(
        tmp_path,
        """
        import os

        def test_fork():
            pid = os.fork()
            if pid == 0:
                os._exit(0)
            assert os.waitpid(pid, 0)[1] == 0
        """,
        dump_dir=dump_dir,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _single(dump_dir, "stacks") == ""


def test_dumps_stack_on_sigterm_and_still_terminates(tmp_path: Path) -> None:
    dump_dir = tmp_path / "dump"
    result = _run(
        tmp_path,
        """
        import os
        import signal

        def test_terminated():
            os.kill(os.getpid(), signal.SIGTERM)
        """,
        dump_dir=dump_dir,
    )

    assert result.returncode == -signal.SIGTERM
    assert "test_terminated" in _single(dump_dir, "SIGTERM")


def test_controller_records_how_crashed_worker_died(tmp_path: Path) -> None:
    dump_dir = tmp_path / "dump"
    result = _run(
        tmp_path,
        """
        import os
        import signal

        def test_killed():
            os.kill(os.getpid(), signal.SIGKILL)
        """,
        dump_dir=dump_dir,
        extra_args=("-n", "1", "--max-worker-restart=0"),
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "worker gw0 宕机（Not properly terminated），进程被信号 SIGKILL 终止" in _single(dump_dir, "progress")


def test_writes_nothing_without_dump_dir(tmp_path: Path) -> None:
    result = _run(tmp_path, "def test_ok():\n    pass\n", dump_dir=None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert [*tmp_path.rglob("*.progress"), *tmp_path.rglob("*.stacks")] == []
