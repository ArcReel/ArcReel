"""Import smoke test — catches circular deps and import-time side effects.

参数化遍历 lib/ 与 server/ 下核心子模块，每个 importlib.import_module 一次。
任何循环依赖、缺失依赖、顶层副作用崩溃都会在此红。

同进程遍历只能发现"任何顺序都炸"的环；只在特定模块作为首个导入时才触发的环，会被先前
用例留在 ``sys.modules`` 里的缓存掩盖。故另有 :func:`test_module_imports_first_in_fresh_process`
在全新解释器里逐个验证首位导入。
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# 核心子模块白名单。新增包时请在此追加（而不是用 pkgutil.walk_packages，
# 以避免意外拉起 lib.i18n.zh/en 的翻译数据包和 alembic.versions 迁移脚本）。
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
    "lib.status_calculator",
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


# 首位导入必须成立的模块：``lib.config`` ↔ ``lib.custom_provider`` ↔ 各媒体 backend 三者
# 相互引用，环只在特定模块打头时才显形，故逐个在全新解释器里验证。
FIRST_IMPORT_MODULES = [
    "lib.config",
    "lib.config.resolver",
    "lib.custom_provider.backends",
    "lib.custom_provider.capabilities",
    "lib.custom_provider.discovery",
    "lib.custom_provider.endpoints",
    "lib.custom_provider.factory",
    "lib.custom_provider.loader",
]


@pytest.mark.unit
@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports_cleanly(module_name: str) -> None:
    importlib.import_module(module_name)


@pytest.mark.unit
@pytest.mark.parametrize("module_name", FIRST_IMPORT_MODULES)
def test_module_imports_first_in_fresh_process(module_name: str) -> None:
    """该模块作为解释器里第一个被导入的项目模块时也能成功。

    ``lib.config`` 与 ``lib.custom_provider`` 互相引用（后者装配 backend、backend 又读前者的
    URL 工具），一旦某条边退回模块级导入就会成环——而环只在特定模块打头时才炸，同进程的
    冒烟遍历因 ``sys.modules`` 已被前序用例填热而看不见。全新子进程是唯一能钉住"任意顺序
    均可独立导入"的手段。
    """
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{module_name} 无法作为首个导入：\n{result.stderr}"
