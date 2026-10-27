"""挂起诊断插件（`tests/hang_watchdog.py`）的端到端契约。

每条用例在临时目录里另起一个 pytest 会话，只加载该插件。契约：

- 会话结束后进程迟迟不退出（被非 daemon 线程挡住）时，转储里有当前阶段与挡路线程的调用栈。
- worker 被直接杀掉时，进展文件的最后一条是它正在执行的用例。
- 未设转储目录时插件不写任何文件。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(root: Path, source: str, *, dump_dir: Path | None) -> subprocess.CompletedProcess[str]:
    (root / "test_probe.py").write_text(textwrap.dedent(source), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT), "ARCREEL_TEST_HANG_IDLE_SECONDS": "0.4"}
    env.pop("ARCREEL_TEST_HANG_DUMP_DIR", None)
    env.pop("PYTEST_XDIST_WORKER", None)
    if dump_dir is not None:
        env["ARCREEL_TEST_HANG_DUMP_DIR"] = str(dump_dir)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.hang_watchdog", "-p", "no:cacheprovider", "-q", "test_probe.py"],
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


def test_progress_names_test_running_when_process_killed(tmp_path: Path) -> None:
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


def test_writes_nothing_without_dump_dir(tmp_path: Path) -> None:
    result = _run(tmp_path, "def test_ok():\n    pass\n", dump_dir=None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert [*tmp_path.rglob("*.progress"), *tmp_path.rglob("*.stacks")] == []
