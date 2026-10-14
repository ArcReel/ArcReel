"""事件循环阻塞护栏（`tests/blocking_guard`）的端到端契约。

每条用例在临时目录里另起一个 pytest 会话，加载护栏插件，被测样本放在该目录的 ``lib/`` 与
``server/`` 下（护栏按 rootdir 下的这两个目录判定业务代码），用例与替身放在 ``tests/`` 下，
锁取真实的 lib 实现。契约：

- 阻塞调用被业务代码的宽泛异常捕获包住，用例照样失败；放进工作线程的同一调用、进程级缓存的
  加载器函数体不算违规，有容量上限、按参数缓存的加载器仍算。
- 同步用例经 ``TestClient`` 进入的应用代码同样受检。
- lib/server 的 async 函数经 ``tests/`` 下的替身触发的阻塞记到该 async 函数名下；测试直接调用同步
  lib 代码、测试内联 ``to_thread`` 的阻塞不计。
- asyncpg 建连时解析连接参数的文件探测不记到调用方头上。
- 在事件循环线程上阻塞地获取项目文件锁、版本管理锁记为违规，非阻塞的尝试加锁不算。
- 永久豁免清单里的条目放行，没有别的豁免途径；完整运行时未触发的豁免条目让会话失败，部分运行
  （含 ``--ignore`` 跳过路径、``--collect-only`` 等不执行用例的模式）不报。
- 带 ``--blocking-guard-expect-all-exemptions`` 时，按路径或关键字筛选的运行同样报未触发的豁免。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXEMPTIONS = Path("tests/blocking_guard/exemptions.toml")
# 用例与替身放在会话目录的 tests/ 下：护栏按这个目录区分测试自身驱动的阻塞。
_PROBE_RELPATH = Path("tests/test_probe.py")

_SAMPLES = """
import asyncio
import contextlib
from functools import cache, lru_cache
from pathlib import Path

import asyncpg
import portalocker

from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.version_manager import VersionManager


async def read_or_default(path: Path) -> str:
    try:
        return path.read_text()
    except Exception:
        return "default"


async def read_in_thread(path: Path) -> str:
    return await asyncio.to_thread(path.read_text)


def read_sync(path: Path) -> str:
    return path.read_text()


async def load_with(loader, path: Path) -> str:
    return loader(path)


async def load_with_async(loader, path: Path) -> str:
    return await loader(path)


@cache
def load_once(path: Path) -> str:
    return path.read_text()


async def read_cached(path: Path) -> str:
    return load_once(path)


@lru_cache(maxsize=1)
def load_bounded(path: Path) -> str:
    return path.read_text()


async def read_bounded_cache(path: Path) -> str:
    return load_bounded(path)


async def exempted_read(path: Path) -> str:
    return path.read_text()


async def hold_project_lock(project_dir: Path) -> None:
    with project_metadata_lock(project_dir):
        pass


async def try_lock_once(lock_file: Path) -> bool:
    handle = await asyncio.to_thread(lock_file.open, "a+b")
    try:
        portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        portalocker.unlock(handle)
        return True
    finally:
        await asyncio.to_thread(handle.close)


async def read_versions(project_dir: Path) -> dict:
    return VersionManager(project_dir).get_versions("storyboards", "E1S01")


async def connect_unreachable_postgres() -> None:
    with contextlib.suppress(OSError):
        await asyncpg.connect("postgresql://arcreel@127.0.0.1:1/arcreel", timeout=5)
"""

_APP = """
from pathlib import Path

from fastapi import FastAPI

app = FastAPI()


@app.get("/read")
async def read(path: str) -> dict:
    return {"text": Path(path).read_text()}
"""

_PROBE = """
import asyncio

from fastapi.testclient import TestClient

import blocking_samples as samples
from blocking_app import app


async def test_swallowed_read(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    assert await samples.read_or_default(target) == "content"


async def test_offloaded_read(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    assert await samples.read_in_thread(target) == "content"


def _disk_loader(path):
    return path.read_text()


async def _async_disk_loader(path):
    return _disk_loader(path)


async def test_blocking_test_double(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    assert await samples.load_with(_disk_loader, target) == "content"


async def test_blocking_async_test_double(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    assert await samples.load_with_async(_async_disk_loader, target) == "content"


async def test_test_calls_sync_lib_directly(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    assert samples.read_sync(target) == "content"


async def test_test_offloads_sync_lib_inline(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    assert await asyncio.to_thread(samples.read_sync, target) == "content"


async def test_cached_loader(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    assert await samples.read_cached(target) == "content"


async def test_bounded_cache_loader(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    assert await samples.read_bounded_cache(target) == "content"


def test_sync_testclient_route(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    with TestClient(app) as client:
        assert client.get("/read", params={"path": str(target)}).json() == {"text": "content"}


async def test_exempted_read(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    assert await samples.exempted_read(target) == "content"


async def test_project_file_lock(tmp_path):
    await samples.hold_project_lock(tmp_path)


async def test_nonblocking_try_lock(tmp_path):
    assert await samples.try_lock_once(tmp_path / "x.lock")


async def test_version_lock(tmp_path):
    assert await samples.read_versions(tmp_path) == {"current_version": 0, "versions": []}


async def test_postgres_connect():
    await samples.connect_unreachable_postgres()
"""


def _make_session_dir(root: Path, exemptions: dict[str, str]) -> Path:
    (root / "lib").mkdir(parents=True)
    (root / "server").mkdir()
    (root / "lib" / "blocking_samples.py").write_text(textwrap.dedent(_SAMPLES), encoding="utf-8")
    (root / "server" / "blocking_app.py").write_text(textwrap.dedent(_APP), encoding="utf-8")
    (root / "tests").mkdir()
    (root / _PROBE_RELPATH).write_text(textwrap.dedent(_PROBE), encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nasyncio_mode = "auto"\npythonpath = ["lib", "server"]\n',
        encoding="utf-8",
    )
    exemptions_file = root / _EXEMPTIONS
    exemptions_file.parent.mkdir(parents=True)
    exemptions_file.write_text(
        "".join(f'[[exemption]]\nkey = "{key}"\nreason = "{reason}"\n\n' for key, reason in exemptions.items()),
        encoding="utf-8",
    )
    return root


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(_REPO_ROOT), env.get("PYTHONPATH")]))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.blocking_guard", "-p", "no:cacheprovider", "-rA", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _outcomes(output: str) -> dict[str, str]:
    pattern = r"^(PASSED|FAILED|ERROR) tests/test_probe\.py::(\w+)"
    return {name: outcome for outcome, name in re.findall(pattern, output, re.M)}


def _failure_section(output: str, test_name: str) -> str:
    match = re.search(rf"_+ {test_name} _+\n(.*?)(?=\n_{{3,}} |\n=+ )", output, re.S)
    assert match, output
    return match.group(1)


@pytest.fixture(scope="module")
def probe_run(tmp_path_factory: pytest.TempPathFactory) -> subprocess.CompletedProcess[str]:
    root = _make_session_dir(
        tmp_path_factory.mktemp("session"),
        {"lib/blocking_samples.py::exempted_read": "样本：模拟启动路径上的一次性阻塞"},
    )
    return _run(root)


def test_blocking_call_fails_the_test_even_when_business_code_swallows_errors(probe_run):
    output = probe_run.stdout + probe_run.stderr
    assert _outcomes(output)["test_swallowed_read"] == "FAILED", output
    section = _failure_section(output, "test_swallowed_read")
    assert "lib/blocking_samples.py::read_or_default" in section
    assert "io.TextIOWrapper.read" in section


def test_offloaded_cached_and_exempted_calls_pass(probe_run):
    outcomes = _outcomes(probe_run.stdout)
    assert outcomes["test_offloaded_read"] == "PASSED", probe_run.stdout
    assert outcomes["test_exempted_read"] == "PASSED", probe_run.stdout
    assert outcomes["test_nonblocking_try_lock"] == "PASSED", probe_run.stdout
    assert outcomes["test_cached_loader"] == "PASSED", probe_run.stdout


@pytest.mark.parametrize(
    ("test_name", "key"),
    [
        ("test_blocking_test_double", "lib/blocking_samples.py::load_with"),
        ("test_blocking_async_test_double", "lib/blocking_samples.py::load_with_async"),
    ],
)
def test_blocking_inside_a_test_double_is_charged_to_the_enclosing_business_coroutine(probe_run, test_name, key):
    # 替身在 tests/ 下读写磁盘，换成生产依赖时同一位置同样阻塞事件循环，记到包住它的 lib async 函数名下。
    assert _outcomes(probe_run.stdout)[test_name] == "FAILED", probe_run.stdout
    section = _failure_section(probe_run.stdout, test_name)
    assert key in section
    assert "tests/test_probe.py" in section


def test_blocking_driven_by_the_test_itself_is_not_a_violation(probe_run):
    outcomes = _outcomes(probe_run.stdout)
    assert outcomes["test_test_calls_sync_lib_directly"] == "PASSED", probe_run.stdout
    assert outcomes["test_test_offloads_sync_lib_inline"] == "PASSED", probe_run.stdout


def test_postgres_driver_connection_setup_is_not_charged_to_the_caller(probe_run):
    # asyncpg 建连时在事件循环上探测 ~/.pgpass 与 ~/.postgresql 下的证书文件，每条新连接一次，
    # 归属落在恰好让连接池新建连接的那个调用方上，随运行顺序变化。
    assert _outcomes(probe_run.stdout)["test_postgres_connect"] == "PASSED", probe_run.stdout


def test_bounded_per_argument_cache_is_still_checked(probe_run):
    assert _outcomes(probe_run.stdout)["test_bounded_cache_loader"] == "FAILED", probe_run.stdout
    assert "lib/blocking_samples.py::read_bounded_cache" in _failure_section(
        probe_run.stdout, "test_bounded_cache_loader"
    )


def test_app_code_behind_sync_testclient_is_checked(probe_run):
    assert _outcomes(probe_run.stdout)["test_sync_testclient_route"] == "FAILED", probe_run.stdout
    assert "server/blocking_app.py::read" in _failure_section(probe_run.stdout, "test_sync_testclient_route")


@pytest.mark.parametrize(
    ("test_name", "key", "rule"),
    [
        ("test_project_file_lock", "lib/blocking_samples.py::hold_project_lock", "文件锁（portalocker 阻塞模式）"),
        ("test_version_lock", "lib/blocking_samples.py::read_versions", "版本管理锁（RLock）"),
    ],
)
def test_blocking_lock_acquisition_on_the_loop_is_a_violation(probe_run, test_name, key, rule):
    assert _outcomes(probe_run.stdout)[test_name] == "FAILED", probe_run.stdout
    section = _failure_section(probe_run.stdout, test_name)
    assert key in section
    assert rule in section


def test_full_run_fails_on_untriggered_exemptions_but_partial_run_does_not(tmp_path: Path):
    stale = "lib/blocking_samples.py::never_called"
    root = _make_session_dir(tmp_path / "session", {stale: "样本：从未触发的豁免"})

    full = _run(root)
    partial = _run(root, "-k", "offloaded")

    assert full.returncode == pytest.ExitCode.TESTS_FAILED, full.stdout
    assert f"永久豁免未触发，请从 {_EXEMPTIONS} 删除：{stale}" in full.stdout
    assert partial.returncode == 0, partial.stdout
    assert stale not in partial.stdout


@pytest.mark.parametrize(
    "selection",
    [("tests/test_probe.py::test_offloaded_read",), ("-k", "offloaded"), ("tests",)],
)
def test_expect_all_exemptions_option_reports_untriggered_exemptions_on_selected_runs(
    tmp_path: Path, selection: tuple[str, ...]
):
    # CI 按 tests/unit、tests/integration 分路径跑，两边都是部分运行；unit job 带上这个选项，
    # 删掉或改了启动路径后，过期的豁免条目在 CI 里就报出来。
    stale = "lib/blocking_samples.py::never_called"
    root = _make_session_dir(tmp_path / "session", {stale: "样本：从未触发的豁免"})

    run = _run(root, "--blocking-guard-expect-all-exemptions", *selection)

    assert run.returncode == pytest.ExitCode.TESTS_FAILED, run.stdout
    assert f"永久豁免未触发，请从 {_EXEMPTIONS} 删除：{stale}" in run.stdout


def test_expect_all_exemptions_option_passes_when_every_exemption_is_triggered(tmp_path: Path):
    root = _make_session_dir(
        tmp_path / "session",
        {"lib/blocking_samples.py::exempted_read": "样本：模拟启动路径上的一次性阻塞"},
    )

    run = _run(root, "--blocking-guard-expect-all-exemptions", "tests/test_probe.py::test_exempted_read")

    assert run.returncode == 0, run.stdout
    assert "永久豁免未触发" not in run.stdout


@pytest.mark.parametrize(
    "partial_args",
    [("--ignore=tests/test_probe.py",), ("--ignore-glob=*_probe.py",), ("--collect-only",), ("--setup-only",)],
)
def test_runs_that_skip_or_do_not_execute_tests_do_not_report_untriggered_exemptions(
    tmp_path: Path, partial_args: tuple[str, ...]
):
    stale = "lib/blocking_samples.py::never_called"
    root = _make_session_dir(tmp_path / "session", {stale: "样本：从未触发的豁免"})

    partial = _run(root, *partial_args)

    assert stale not in partial.stdout, partial.stdout
