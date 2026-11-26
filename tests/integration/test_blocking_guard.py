"""事件循环阻塞护栏（`tests/blocking_guard`）的端到端契约。

本文件的用例共享临时目录中一个加载护栏插件的 pytest 会话。被测样本放在该目录的 ``lib/`` 与
``server/`` 下（护栏按 rootdir 下的这两个目录判定业务代码），用例与替身放在 ``tests/`` 下，
锁取真实的 lib 实现。契约：

- 阻塞调用被业务代码的宽泛异常捕获包住，用例照样失败；放进工作线程的同一调用、进程级缓存的
  加载器函数体不算违规，有容量上限、按参数缓存的加载器仍算。
- 同步用例经 ``TestClient`` 进入的应用代码同样受检。
- lib/server 的 async 函数经 ``tests/`` 下的替身触发的阻塞记到该 async 函数名下；测试直接调用同步
  lib 代码、测试内联 ``to_thread`` 的阻塞不计。
- asyncpg 建连时解析连接参数的文件探测、coverage 等待自身数据锁，都不记到调用方头上。
- 报错的栈预览除 lib/server/tests 帧外，按真实调用顺序保留拦截点到第一个仓库帧之间最内层的几条
  非仓库帧（标准库、``site-packages`` 下的第三方代码），路径取相对形式；超出上限的注明省略条数。
- 在事件循环线程上阻塞地获取项目文件锁、版本管理锁记为违规，非阻塞的尝试加锁不算。
- 永久豁免清单里的条目放行。不同会话范围的清单完整性由同目录的豁免测试覆盖。
"""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.blocking_guard_probe import run_probe, write_probe

_SAMPLES = """
import asyncio
import contextlib
import threading
from functools import cache, lru_cache
from pathlib import Path

import asyncpg
import blocking_vendor
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


async def measured_noop() -> None:
    return None


async def wait_on_event(event: threading.Event) -> bool:
    return event.wait(0.01)


async def acquire_via_vendor(lock: threading.Lock, depth: int) -> None:
    blocking_vendor.acquire_nested(lock, depth)
"""

_VENDOR_RELPATH = Path("vendor/site-packages/blocking_vendor.py")
_VENDOR = """
def acquire_nested(lock, depth):
    if depth:
        return acquire_nested(lock, depth - 1)
    return acquire_now(lock)


def acquire_now(lock):
    if lock.acquire(timeout=0.01):
        lock.release()
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

import sys
import threading

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


def _held_lock():
    # 一把已被持有、不再释放的锁：样本带超时去拿，护栏一定看到它被占用，结果不取决于线程交错。
    lock = threading.Lock()
    lock.acquire()
    return lock


async def test_lock_in_stdlib_internals():
    assert not await samples.wait_on_event(threading.Event())


async def test_lock_in_third_party_code():
    await samples.acquire_via_vendor(_held_lock(), 0)


async def test_lock_deep_in_third_party_code():
    await samples.acquire_via_vendor(_held_lock(), 8)


async def test_coverage_data_lock():
    import coverage

    # 另一个线程持有 coverage 的数据锁时，事件循环线程进入被测协程，tracer 在 lock_data 里等这把锁；
    # 释放线程看到它停在 lock_data 里才放锁。
    loop_thread = threading.get_ident()
    waited = threading.Event()
    done = threading.Event()

    def release_once_waiting(data_lock):
        while not done.is_set():
            frame = sys._current_frames().get(loop_thread)
            while frame is not None and frame.f_code.co_name != "lock_data":
                frame = frame.f_back
            if frame is not None:
                waited.set()
                break
            done.wait(0.001)
        data_lock.release()

    cov = coverage.Coverage(data_file=None, concurrency=["thread"], include=["*/blocking_samples.py"])
    cov.start()
    try:
        data_lock = cov._collector.data_lock
        data_lock.acquire()
        releaser = threading.Thread(target=release_once_waiting, args=(data_lock,))
        releaser.start()
        await samples.measured_noop()
        done.set()
        releaser.join()
    finally:
        cov.stop()
    assert waited.is_set()
"""


def _outcomes(output: str) -> dict[str, str]:
    pattern = r"^(PASSED|FAILED|ERROR) tests/test_probe\.py::(\w+)"
    return {name: outcome for outcome, name in re.findall(pattern, output, re.M)}


def _failure_section(output: str, test_name: str) -> str:
    match = re.search(rf"_+ {test_name} _+\n(.*?)(?=\n_{{3,}} |\n=+ )", output, re.S)
    assert match, output
    return match.group(1)


@pytest.fixture(scope="module")
def probe_run(tmp_path_factory: pytest.TempPathFactory) -> subprocess.CompletedProcess[str]:
    root = tmp_path_factory.mktemp("session")
    write_probe(
        root,
        samples=textwrap.dedent(_SAMPLES),
        tests=textwrap.dedent(_PROBE),
        exemptions={"lib/blocking_samples.py::exempted_read": "样本：模拟启动路径上的一次性阻塞"},
    )
    (root / "server").mkdir()
    (root / "server" / "blocking_app.py").write_text(textwrap.dedent(_APP), encoding="utf-8")
    (root / _VENDOR_RELPATH).parent.mkdir(parents=True)
    (root / _VENDOR_RELPATH).write_text(textwrap.dedent(_VENDOR), encoding="utf-8")
    return run_probe(root)


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
    assert _outcomes(probe_run.stdout)[test_name] == "FAILED", probe_run.stdout
    section = _failure_section(probe_run.stdout, test_name)
    assert key in section
    assert "tests/test_probe.py" in section


def test_blocking_driven_by_the_test_itself_is_not_a_violation(probe_run):
    outcomes = _outcomes(probe_run.stdout)
    assert outcomes["test_test_calls_sync_lib_directly"] == "PASSED", probe_run.stdout
    assert outcomes["test_test_offloads_sync_lib_inline"] == "PASSED", probe_run.stdout


def test_postgres_driver_connection_setup_is_not_charged_to_the_caller(probe_run):
    assert _outcomes(probe_run.stdout)["test_postgres_connect"] == "PASSED", probe_run.stdout


def test_coverage_waiting_for_its_own_data_lock_is_not_charged_to_the_measured_code(probe_run):
    assert _outcomes(probe_run.stdout)["test_coverage_data_lock"] == "PASSED", probe_run.stdout


def test_bounded_per_argument_cache_is_still_checked(probe_run):
    assert _outcomes(probe_run.stdout)["test_bounded_cache_loader"] == "FAILED", probe_run.stdout
    assert "lib/blocking_samples.py::read_bounded_cache" in _failure_section(
        probe_run.stdout, "test_bounded_cache_loader"
    )


def test_app_code_behind_sync_testclient_is_checked(probe_run):
    assert _outcomes(probe_run.stdout)["test_sync_testclient_route"] == "FAILED", probe_run.stdout
    assert "server/blocking_app.py::read" in _failure_section(probe_run.stdout, "test_sync_testclient_route")


def _preview_lines(section: str, *patterns: str) -> list[int]:
    """各模式在栈预览里命中的行号，按模式顺序；未命中时断言失败。"""
    lines = section.splitlines()
    positions = []
    for pattern in patterns:
        hits = [i for i, line in enumerate(lines) if re.fullmatch(rf"\s+{pattern}", line)]
        assert hits, f"{pattern!r} 不在栈预览中：\n{section}"
        positions.append(hits[0])
    return positions


def test_preview_keeps_stdlib_frames_between_the_blocking_call_and_business_code(probe_run):
    assert _outcomes(probe_run.stdout)["test_lock_in_stdlib_internals"] == "FAILED", probe_run.stdout
    section = _failure_section(probe_run.stdout, "test_lock_in_stdlib_internals")
    assert "lib/blocking_samples.py::wait_on_event  ← lock.acquire" in section
    positions = _preview_lines(
        section,
        r"threading\.py:\d+ in Condition\.wait",
        r"threading\.py:\d+ in Event\.wait",
        r"lib/blocking_samples\.py:\d+ in wait_on_event",
        r"tests/test_probe\.py:\d+ in test_lock_in_stdlib_internals",
    )
    assert positions == sorted(positions), section
    assert "blockbuster" not in section


def test_preview_keeps_third_party_frames_in_call_order(probe_run):
    assert _outcomes(probe_run.stdout)["test_lock_in_third_party_code"] == "FAILED", probe_run.stdout
    section = _failure_section(probe_run.stdout, "test_lock_in_third_party_code")
    assert "lib/blocking_samples.py::acquire_via_vendor  ← lock.acquire" in section
    positions = _preview_lines(
        section,
        r"blocking_vendor\.py:\d+ in acquire_now",
        r"blocking_vendor\.py:\d+ in acquire_nested",
        r"lib/blocking_samples\.py:\d+ in acquire_via_vendor",
        r"tests/test_probe\.py:\d+ in test_lock_in_third_party_code",
    )
    assert positions == sorted(positions), section
    assert "site-packages" not in section


def test_preview_caps_third_party_frames_without_dropping_business_frames(probe_run):
    assert _outcomes(probe_run.stdout)["test_lock_deep_in_third_party_code"] == "FAILED", probe_run.stdout
    section = _failure_section(probe_run.stdout, "test_lock_deep_in_third_party_code")
    positions = _preview_lines(
        section,
        r"blocking_vendor\.py:\d+ in acquire_now",
        r"…（省略 6 个非仓库帧）",
        r"lib/blocking_samples\.py:\d+ in acquire_via_vendor",
        r"tests/test_probe\.py:\d+ in test_lock_deep_in_third_party_code",
    )
    assert positions == sorted(positions), section
    assert section.count("in acquire_nested") == 3, section


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
