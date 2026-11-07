"""Isolate mutmut-launched pytest sessions: private basetemp and no macOS system-proxy discovery.

Loaded only by ``pyproject.toml`` ``[tool.mutmut] pytest_add_cli_args`` through ``-p tests.mutmut_plugin``;
normal local and CI pytest runs do not load it.

mutmut forks parallel pytest sessions per mutant. Two fork-related measures:

- At teardown they otherwise clean the shared-root ``pytest-current`` symlink simultaneously. Racing
  ``FileNotFoundError`` escapes ``pytest.main``, making children exit normally with code 1 counted as killed.
  Explicit basetemp registers no cleanup; this session removes its own directory during unconfigure.
- On macOS, ``urllib.request.getproxies()`` without ``*_proxy`` environment variables reaches ``_scproxy``
  and SystemConfiguration for system proxies. That framework segfaults in forked multithreaded children.
  Cases constructing ``openai.OpenAI`` / ``httpx.Client`` all encounter this, recording whole mutant batches
  as −11. Replace both macOS-specific entry points with constant no-system-proxy implementations: tests
  never rely on actual system proxies, while environment-provided proxies remain effective through their original path.
"""

from __future__ import annotations

import shutil
import tempfile
import urllib.request

import pytest


def _no_system_proxies() -> dict[str, str]:
    return {}


def _never_bypass_by_system_settings(host: str) -> bool:
    return False


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """``tryfirst`` assigns before pytest's tmpdir plugin reads ``config.option.basetemp``.

    The parsed option and xdist worker assignment are both str; preserve that type here.
    """
    if hasattr(urllib.request, "getproxies_macosx_sysconf"):
        urllib.request.getproxies_macosx_sysconf = _no_system_proxies
        urllib.request.proxy_bypass_macosx_sysconf = _never_bypass_by_system_settings
    if config.option.basetemp is not None:
        return
    private_basetemp = tempfile.mkdtemp(prefix="arcreel-mutmut-basetemp-")
    config.option.basetemp = private_basetemp
    config.add_cleanup(lambda: shutil.rmtree(private_basetemp, ignore_errors=True))
