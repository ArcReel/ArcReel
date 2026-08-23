"""Import smoke test — catches circular deps and import-time side effects.

参数化遍历 lib/ 与 server/ 下核心子模块，每个 importlib.import_module 一次。
任何循环依赖、缺失依赖、顶层副作用崩溃都会在此红。

同进程遍历有其盲区，:func:`test_module_imports_first_in_fresh_process` 补上，理由见该函数。
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# 核心子模块白名单。新增包时请在此追加（而不是用 pkgutil.walk_packages，
# 以避免意外拉起 lib.i18n.zh/en 的翻译数据包和 alembic.versions 迁移脚本）。
# 参与 lib.config ↔ lib.custom_provider 互引的模块另需登记 FIRST_IMPORT_MODULES。
MODULES = [
    # lib 顶层单文件模块
    "lib.ark_shared",
    "lib.asset_fingerprints",
    "lib.cost_calculator",
    "lib.data_validator",
    "lib.gemini_shared",
    "lib.generation_queue",
    "lib.generation_queue_client",
    "lib.generation_worker",
    "lib.grid_manager",
    "lib.grok_shared",
    "lib.image_utils",
    "lib.logging_config",
    "lib.media_generator",
    "lib.openai_shared",
    "lib.project_change_hints",
    "lib.project_manager",
    "lib.prompt_builders",
    "lib.prompt_builders_script",
    "lib.prompt_utils",
    "lib.providers",
    "lib.retry",
    "lib.script_generator",
    "lib.script_models",
    "lib.storyboard_sequence",
    "lib.style_templates",
    "lib.system_config",
    "lib.text_generator",
    "lib.thumbnail",
    "lib.version_manager",
    # lib 子包
    "lib.config",
    "lib.custom_provider",
    "lib.db",
    "lib.db.models",
    "lib.db.repositories",
    "lib.grid",
    "lib.image_backends",
    "lib.text_backends",
    "lib.video_backends",
    # server
    "server",
    "server.agent_runtime",
    "server.app",
    "server.auth",
    "server.dependencies",
    "server.routers",
    "server.services",
]


# 首位导入必须成立的模块，逐个在全新解释器里验证（理由见用例 docstring）。
FIRST_IMPORT_MODULES = [
    "lib.config.resolver",
    "lib.custom_provider.backends",
    "lib.custom_provider.capabilities",
    "lib.custom_provider.discovery",
    "lib.custom_provider.endpoints",
    "lib.custom_provider.factory",
    "lib.custom_provider.loader",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports_cleanly(module_name: str) -> None:
    importlib.import_module(module_name)


@pytest.mark.parametrize("module_name", FIRST_IMPORT_MODULES)
def test_module_imports_first_in_fresh_process(module_name: str) -> None:
    """该模块作为解释器里第一个被导入的项目模块时也能成功。

    ``lib.config`` 与 ``lib.custom_provider`` 互相引用（后者装配 backend、backend 又读前者的
    URL 工具），一旦某条边退回模块级导入就会成环——而环只在特定模块打头时才炸，同进程的
    冒烟遍历因 ``sys.modules`` 已被前序用例填热而看不见。全新子进程是唯一能锁定"任意顺序
    均可独立导入"的手段。
    """
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
        # 导入期的阻塞式副作用（网络、锁等待）正是本用例要拦的形态之一，超时须显式转红，
        # 而不是把 CI job 挂满时限。
        pytest.fail(f"{module_name} 首位导入超时，疑似存在阻塞式顶层副作用")
    assert result.returncode == 0, f"{module_name} 无法作为首个导入：\n{result.stderr}"
