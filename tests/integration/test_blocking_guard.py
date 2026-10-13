"""事件循环阻塞护栏（`tests/blocking_guard`）的端到端契约。

每条用例在临时目录里另起一个 pytest 会话，加载护栏插件，被测样本放在该目录的 ``lib/`` 与
``server/`` 下（护栏按 rootdir 下的这两个目录判定业务代码），锁取真实的 lib 实现。契约：

- 阻塞调用被业务代码的宽泛异常捕获包住，用例照样失败；放进工作线程的同一调用、进程级缓存的
  加载器函数体不算违规。
- 同步用例经 ``TestClient`` 进入的应用代码同样受检。
- 在事件循环线程上阻塞地获取项目文件锁、版本管理锁记为违规，非阻塞的尝试加锁不算。
- 白名单里的条目放行；完整运行时未触发的条目以警告列出，部分运行不报。
- 写出模式把全部违规整体写成白名单，部分运行拒绝写出。
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
_WRITE_ENV = "ARCREEL_BLOCKING_ALLOWLIST_WRITE"
_ALLOWLIST = Path("tests/blocking_guard/allowlist.txt")

_SAMPLES = """
import asyncio
from functools import cache
from pathlib import Path

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


@cache
def load_once(path: Path) -> str:
    return path.read_text()


async def read_cached(path: Path) -> str:
    return load_once(path)


async def allowlisted_read(path: Path) -> str:
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


async def test_cached_loader(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    assert await samples.read_cached(target) == "content"


def test_sync_testclient_route(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    with TestClient(app) as client:
        assert client.get("/read", params={"path": str(target)}).json() == {"text": "content"}


async def test_allowlisted_read(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("content")
    assert await samples.allowlisted_read(target) == "content"


async def test_project_file_lock(tmp_path):
    await samples.hold_project_lock(tmp_path)


async def test_nonblocking_try_lock(tmp_path):
    assert await samples.try_lock_once(tmp_path / "x.lock")


async def test_version_lock(tmp_path):
    assert await samples.read_versions(tmp_path) == {"current_version": 0, "versions": []}
"""


def _make_session_dir(root: Path, allowlist: list[str]) -> Path:
    (root / "lib").mkdir(parents=True)
    (root / "server").mkdir()
    (root / "lib" / "blocking_samples.py").write_text(textwrap.dedent(_SAMPLES), encoding="utf-8")
    (root / "server" / "blocking_app.py").write_text(textwrap.dedent(_APP), encoding="utf-8")
    (root / "test_probe.py").write_text(textwrap.dedent(_PROBE), encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nasyncio_mode = "auto"\npythonpath = ["lib", "server"]\n',
        encoding="utf-8",
    )
    allowlist_file = root / _ALLOWLIST
    allowlist_file.parent.mkdir(parents=True)
    allowlist_file.write_text("".join(f"{entry}\n" for entry in allowlist), encoding="utf-8")
    return root


def _run(root: Path, *args: str, write: bool = False) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != _WRITE_ENV}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(_REPO_ROOT), env.get("PYTHONPATH")]))
    if write:
        env[_WRITE_ENV] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.blocking_guard", "-p", "no:cacheprovider", "-rA", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _outcomes(output: str) -> dict[str, str]:
    pattern = r"^(PASSED|FAILED|ERROR) test_probe\.py::(\w+)"
    return {name: outcome for outcome, name in re.findall(pattern, output, re.M)}


def _failure_section(output: str, test_name: str) -> str:
    match = re.search(rf"_+ {test_name} _+\n(.*?)(?=\n_{{3,}} |\n=+ )", output, re.S)
    assert match, output
    return match.group(1)


@pytest.fixture(scope="module")
def probe_run(tmp_path_factory: pytest.TempPathFactory) -> subprocess.CompletedProcess[str]:
    root = _make_session_dir(tmp_path_factory.mktemp("session"), ["lib/blocking_samples.py::allowlisted_read"])
    return _run(root)


def test_blocking_call_fails_the_test_even_when_business_code_swallows_errors(probe_run):
    output = probe_run.stdout + probe_run.stderr
    assert _outcomes(output)["test_swallowed_read"] == "FAILED", output
    section = _failure_section(output, "test_swallowed_read")
    assert "lib/blocking_samples.py::read_or_default" in section
    assert "io.TextIOWrapper.read" in section


def test_offloaded_cached_and_allowlisted_calls_pass(probe_run):
    outcomes = _outcomes(probe_run.stdout)
    assert outcomes["test_offloaded_read"] == "PASSED", probe_run.stdout
    assert outcomes["test_allowlisted_read"] == "PASSED", probe_run.stdout
    assert outcomes["test_nonblocking_try_lock"] == "PASSED", probe_run.stdout
    assert outcomes["test_cached_loader"] == "PASSED", probe_run.stdout


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


def test_full_run_warns_about_untriggered_allowlist_entries(tmp_path: Path):
    stale = "lib/blocking_samples.py::never_called"
    root = _make_session_dir(tmp_path / "session", [stale])

    full = _run(root)
    partial = _run(root, "-k", "offloaded")

    assert f"临时白名单未触发：{stale}" in full.stdout
    assert partial.returncode == 0, partial.stdout
    assert stale not in partial.stdout


def test_write_mode_regenerates_the_allowlist_from_a_full_run(tmp_path: Path):
    root = _make_session_dir(tmp_path / "session", ["lib/blocking_samples.py::never_called"])

    refused = _run(root, "-k", "offloaded", write=True)
    written = _run(root, write=True)

    assert refused.returncode == pytest.ExitCode.USAGE_ERROR, refused.stdout + refused.stderr
    assert written.returncode == pytest.ExitCode.OK, written.stdout
    entries = [
        line.split("#", 1)[0].strip()
        for line in (root / _ALLOWLIST).read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    ]
    assert entries == [
        "lib/blocking_samples.py::allowlisted_read",
        "lib/blocking_samples.py::hold_project_lock",
        "lib/blocking_samples.py::read_or_default",
        "lib/blocking_samples.py::read_versions",
        "server/blocking_app.py::read",
    ]
