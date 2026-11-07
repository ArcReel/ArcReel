"""Test infrastructure remains process-isolated under mutmut's fork model.

After parent-process stats, mutmut forks parallel per-mutant pytest sessions inheriting atexit registrations
and conftest state. Three contracts: normally exiting children must not delete shared temporary databases
(root conftest); each mutmut session uses private basetemp outside shared ``pytest-of-<user>``, preventing
symlink-cleanup races; macOS mutmut sessions bypass ``_scproxy`` system proxies, so httpx/openai construction
does not segfault in forked children (last two in `tests/mutmut_plugin.py`, loaded through
``[tool.mutmut] pytest_add_cli_args``).
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
    """Arguments mutmut actually appends per pytest session; the plugin connects through these, not manual test loading."""
    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        args: list[str] = tomllib.load(fh)["tool"]["mutmut"]["pytest_add_cli_args"]
    assert args[args.index("-p") + 1] == _MUTMUT_PLUGIN
    return args


@pytest.mark.skipif(not hasattr(os, "fork"), reason="平台无 fork")
def test_forked_child_running_atexit_handlers_keeps_shared_test_db_dir():
    db_dir = _owned_test_db_dir()
    assert db_dir.is_dir()

    pid = os.fork()
    if pid == 0:  # pragma: no cover - child process
        # Normal exit runs atexit registrations; run them explicitly before ``os._exit``
        # to avoid rerunning the whole session along the child's pytest call stack.
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
        # Child probe: this session's tmp_path is outside temproot, where pytest creates its shared root.
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
    # The shared root was never created; this session reclaimed both its private basetemp and temporary database.
    assert list(temproot.iterdir()) == []
    assert list(tmpdir.iterdir()) == []


@pytest.mark.skipif(sys.platform != "darwin", reason="_scproxy 只在 macOS 存在")
def test_mutmut_session_resolves_proxies_without_touching_macos_sysconf():
    if os.environ.get(_SCPROXY_PROBE_ENV):
        # Child probe: urllib binds both _scproxy entries at module level; replace them with raising stubs
        # so any system-proxy discovery fails. Without *_proxy variables, system proxies are urllib's sole remaining path.
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
