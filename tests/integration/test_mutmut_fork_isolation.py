"""测试设施在 mutmut 的 fork 模型下保持进程隔离。

mutmut 在一个父进程里跑过 stats 之后，按 mutant fork 出多个 pytest 会话并行跑，子进程
继承父进程的 atexit 登记表与 conftest 模块状态。三条契约：走正常退出路径的子进程不得
删掉各子进程共用的临时库（根 conftest）；mutmut 会话各用私有 basetemp，不进共享的
``pytest-of-<user>`` 根，多个会话收尾时不再竞争同一条软链；mutmut 会话在 macOS 上不经
``_scproxy`` 读系统代理，构造 httpx / openai 客户端不会在 fork 出的子进程里段错误
（后两条在 `tests/mutmut_plugin.py`，经 ``[tool.mutmut] pytest_add_cli_args`` 加载）。
"""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SQLITE_URL_PREFIX = "sqlite+aiosqlite:///"
_PROBE_ENV = "ARCREEL_BASETEMP_PROBE"
_SCPROXY_PROBE_ENV = "ARCREEL_SCPROXY_PROBE"
_MUTMUT_PLUGIN = "tests.mutmut_plugin"


def _owned_test_db_dir() -> Path:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(_SQLITE_URL_PREFIX):
        pytest.skip("DATABASE_URL 由外部给定，conftest 不持有临时库")
    return Path(url.removeprefix(_SQLITE_URL_PREFIX)).parent


def _mutmut_pytest_args() -> list[str]:
    """mutmut 实际附加给每个 pytest 会话的参数，插件由此接入而非由测试自行加载。"""
    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        args: list[str] = tomllib.load(fh)["tool"]["mutmut"]["pytest_add_cli_args"]
    assert args[args.index("-p") + 1] == _MUTMUT_PLUGIN
    return args


@pytest.mark.skipif(not hasattr(os, "fork"), reason="平台无 fork")
def test_forked_child_running_atexit_handlers_keeps_shared_test_db_dir():
    db_dir = _owned_test_db_dir()
    assert db_dir.is_dir()

    pid = os.fork()
    if pid == 0:  # pragma: no cover - 子进程
        # 正常退出路径会跑 atexit 登记表；这里显式跑一遍再 ``os._exit``，
        # 免得子进程沿 pytest 的调用栈把整个会话再跑一遍。
        try:
            atexit._run_exitfuncs()
        except BaseException:
            os._exit(3)
        os._exit(0)

    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert db_dir.is_dir()


def test_mutmut_session_uses_private_basetemp_outside_shared_root(tmp_path: Path):
    if os.environ.get(_PROBE_ENV):
        # 子进程里的探针：本会话的 tmp_path 不在 pytest 会建共享根的 temproot 之下。
        assert not tmp_path.is_relative_to(Path(os.environ["PYTEST_DEBUG_TEMPROOT"]).resolve())
        return

    temproot = tmp_path / "temproot"
    temproot.mkdir()
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    env = {
        **os.environ,
        _PROBE_ENV: "1",
        "PYTEST_DEBUG_TEMPROOT": str(temproot),
        "TMPDIR": str(tmpdir),
    }
    nodeid = (
        f"{Path(__file__).relative_to(_REPO_ROOT).as_posix()}"
        f"::{test_mutmut_session_uses_private_basetemp_outside_shared_root.__name__}"
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *_mutmut_pytest_args(), nodeid],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # 共享根从未被建立；私有 basetemp 与临时库都已由该会话自己回收。
    assert list(temproot.iterdir()) == []
    assert list(tmpdir.iterdir()) == []


@pytest.mark.skipif(sys.platform != "darwin", reason="_scproxy 只在 macOS 存在")
def test_mutmut_session_resolves_proxies_without_touching_macos_sysconf():
    if os.environ.get(_SCPROXY_PROBE_ENV):
        # 子进程里的探针：urllib 在模块顶层就绑定了 _scproxy 的两个入口，把它们换成会抛错的桩，
        # 凡走到系统代理发现的路径都会失败。环境里没有 *_proxy 变量，urllib 只剩系统代理这条路。
        import urllib.request

        def _boom(*_args: object) -> object:
            raise AssertionError("mutmut 会话不得调用 SystemConfiguration 读系统代理")

        urllib.request._get_proxies = _boom
        urllib.request._get_proxy_settings = _boom
        assert urllib.request.getproxies() == {}
        assert urllib.request.proxy_bypass("example.com") is False
        return

    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env[_SCPROXY_PROBE_ENV] = "1"
    nodeid = (
        f"{Path(__file__).relative_to(_REPO_ROOT).as_posix()}"
        f"::{test_mutmut_session_resolves_proxies_without_touching_macos_sysconf.__name__}"
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *_mutmut_pytest_args(), nodeid],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
