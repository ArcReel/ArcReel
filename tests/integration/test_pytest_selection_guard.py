"""定向测试选择含不存在的路径时，根 conftest 在收集前报用法错误。

不带 -n 时 pytest 自带「file or directory not found」；xdist 下 controller 只汇总 worker
结果，缺失路径与同批真实文件一起被丢，只剩「no tests ran」与退出码 5。两种模式都要
以退出码 4 与明确的错误行终止，真实文件也不得被静默跑过。``--pyargs`` 下位置参数按
模块名判定，存在的模块照常收集，缺失的模块同样在收集前报错。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXISTING = "tests/integration/test_imports.py"
_MISSING = "tests/integration/does_not_exist_test.py"
_EXISTING_MODULE = "tests.integration.test_imports"
_MISSING_MODULE = "tests.integration.does_not_exist_test"
_USAGE_ERROR_EXIT = 4


def _run_pytest(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
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
def test_pyargs_missing_module_fails_before_collection(dist_args: tuple[str, ...]):
    completed = _run_pytest(*dist_args, "--pyargs", _EXISTING_MODULE, _MISSING_MODULE)

    assert completed.returncode == _USAGE_ERROR_EXIT, completed.stdout + completed.stderr
    assert _MISSING_MODULE in completed.stderr
    assert "passed" not in completed.stdout
