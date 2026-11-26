"""在新解释器中检查首位导入。"""

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def assert_first_import(module_name: str) -> None:
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module_name}"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{module_name} 首位导入超时，疑似存在阻塞式顶层副作用")
    assert result.returncode == 0, f"{module_name} 无法作为首个导入：\n{result.stderr}"
