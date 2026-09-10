"""定向测试选择含不存在的路径时，根 conftest 在收集前报用法错误。

不带 -n 时 pytest 自带「file or directory not found」；xdist 下 controller 只汇总 worker
结果，缺失路径与同批真实文件一起被丢，只剩「no tests ran」与退出码 5。两种模式都要
以退出码 4 与明确的错误行终止，真实文件也不得被静默跑过。``--pyargs`` 下位置参数按
模块名判定，存在的模块照常收集，缺失的模块、``consider_namespace_packages`` 关闭时的
namespace package，以及 ``::`` 之前为空的选择（如 ``::test_x``）同样在收集前报错。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXISTING = "tests/integration/test_imports.py"
_MISSING = "tests/integration/does_not_exist_test.py"
_EXISTING_MODULE = "tests.integration.test_imports"
_MISSING_MODULE = "tests.integration.does_not_exist_test"
_NODE_ONLY = "::test_does_not_exist"
_NAMESPACE_PKG = "arcreel_guard_ns_pkg"
_USAGE_ERROR_EXIT = 4


def _run_pytest(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=None if env is None else {**os.environ, **env},
    )


@pytest.mark.parametrize("dist_args", [(), ("-n", "2", "--dist", "loadfile")], ids=["plain", "xdist"])
def test_missing_selection_path_fails_before_collection(dist_args: tuple[str, ...]):
    completed = _run_pytest(*dist_args, _EXISTING, _MISSING)

    assert completed.returncode == _USAGE_ERROR_EXIT, completed.stdout + completed.stderr
    assert _MISSING in completed.stderr
    assert "passed" not in completed.stdout


def test_pyargs_existing_module_is_collected():
    completed = _run_pytest("--pyargs", _EXISTING_MODULE, "--collect-only")

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("dist_args", [(), ("-n", "2", "--dist", "loadfile")], ids=["plain", "xdist"])
def test_node_only_selector_fails_before_collection(dist_args: tuple[str, ...]):
    completed = _run_pytest(*dist_args, _EXISTING, _NODE_ONLY)

    assert completed.returncode == _USAGE_ERROR_EXIT, completed.stdout + completed.stderr
    assert _NODE_ONLY in completed.stderr
    assert "passed" not in completed.stdout


@pytest.mark.parametrize("dist_args", [(), ("-n", "2", "--dist", "loadfile")], ids=["plain", "xdist"])
def test_pyargs_missing_module_fails_before_collection(dist_args: tuple[str, ...]):
    completed = _run_pytest(*dist_args, "--pyargs", _EXISTING_MODULE, _MISSING_MODULE)

    assert completed.returncode == _USAGE_ERROR_EXIT, completed.stdout + completed.stderr
    assert _MISSING_MODULE in completed.stderr
    assert "passed" not in completed.stdout


@pytest.mark.parametrize("dist_args", [(), ("-n", "2", "--dist", "loadfile")], ids=["plain", "xdist"])
def test_pyargs_unusable_namespace_package_fails_before_collection(dist_args: tuple[str, ...], tmp_path: Path):
    (tmp_path / _NAMESPACE_PKG).mkdir()
    (tmp_path / _NAMESPACE_PKG / "mod.py").write_text("", encoding="utf-8")

    completed = _run_pytest(
        *dist_args,
        "--pyargs",
        _EXISTING_MODULE,
        _NAMESPACE_PKG,
        env={"PYTHONPATH": str(tmp_path)},
    )

    assert completed.returncode == _USAGE_ERROR_EXIT, completed.stdout + completed.stderr
    assert _NAMESPACE_PKG in completed.stderr
    assert "passed" not in completed.stdout
