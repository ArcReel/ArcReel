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
# 参与模块级互引的模块（lib.config ↔ lib.custom_provider、端点定义的分派方 ↔ 各 kind 的实现方）
# 及要求保持轻量的基础模块另需登记 FIRST_IMPORT_MODULES。
MODULES = [
    # lib 顶层单文件模块
    "lib.backends.ark_shared",
    "lib.backends.backend_runtime",
    "lib.backends.video_backend_contract",
    "lib.project.asset_fingerprints",
    "lib.billing.cost_calculator",
    "lib.project.data_validator",
    "lib.backends.gemini_shared",
    "lib.generation.generation_queue",
    "lib.generation.generation_queue_client",
    "lib.generation.generation_worker",
    "lib.script.grid.grid_manager",
    "lib.backends.grok_shared",
    "lib.infra.image_utils",
    "lib.infra.logging_config",
    "lib.generation.media_generator",
    "lib.backends.openai_shared",
    "lib.project.project_change_hints",
    "lib.project.project_manager",
    "lib.prompts.prompt_builders",
    "lib.prompts.prompt_builders_script",
    "lib.prompts.prompt_utils",
    "lib.backends.providers",
    "lib.infra.retry",
    "lib.script.script_generator",
    "lib.script.script_models",
    "lib.script.storyboard_sequence",
    "lib.prompts.style_templates",
    "lib.config.system_config",
    "lib.backends.text_generator",
    "lib.infra.thumbnail",
    "lib.artifacts.version_manager",
    # lib 子包
    "lib.config",
    "lib.custom_provider",
    "lib.custom_provider.comfyui",
    "lib.db",
    "lib.db.models",
    "lib.db.repositories",
    "lib.script.grid",
    "lib.backends.image_backends",
    "lib.prompts.prompt_templates",
    "lib.backends.text_backends",
    "lib.backends.video_backends",
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
    "lib.backends.backend_runtime",
    "lib.backends.image_backends.base",
    "lib.backends.video_backend_contract",
    "lib.config.resolver",
    "lib.custom_provider.backends",
    "lib.custom_provider.capabilities",
    "lib.custom_provider.comfyui.comfyui_backend",
    "lib.custom_provider.comfyui.comfyui_client",
    "lib.custom_provider.comfyui.import_shapes",
    "lib.custom_provider.comfyui.inference",
    "lib.custom_provider.comfyui.request_builder",
    "lib.custom_provider.comfyui.validator",
    "lib.custom_provider.discovery",
    "lib.custom_provider.endpoint_definition",
    "lib.custom_provider.endpoint_test",
    "lib.custom_provider.endpoints",
    "lib.custom_provider.factory",
    "lib.custom_provider.loader",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports_cleanly(module_name: str) -> None:
    """冒烟：模块能按全名导入，import 期副作用（配置读取、循环 import）不抛错。"""
    assert importlib.import_module(module_name).__name__ == module_name


@pytest.mark.parametrize("module_name", FIRST_IMPORT_MODULES)
def test_module_imports_first_in_fresh_process(module_name: str) -> None:
    """该模块作为解释器里第一个被导入的项目模块时也能成功。

    ``lib.config`` 与 ``lib.custom_provider`` 互相引用（后者装配 backend、backend 又读前者的
    URL 工具）；端点定义的分派方与各 ``kind`` 的实现方也隔着包边界互指（``endpoint_definition``
    的分派表 import 各 kind 的校验实现，``comfyui.import_shapes`` 又回头取 ``kinds`` 的常量）。
    这类边一旦有一条退回模块级导入就会成环——而环只在特定模块打头时才炸，同进程的冒烟遍历因
    ``sys.modules`` 已被前序用例填热而看不见。全新子进程是唯一能锁定"任意顺序均可独立导入"的手段。
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


def test_video_backend_contract_first_import_stays_light() -> None:
    """视频契约首位导入不加载运行支持、第三方 HTTP/ORM 或视频实现包。"""
    code = """
import sys
import lib.backends.video_backend_contract

forbidden = {
    "httpx",
    "sqlalchemy",
    "lib.backends.backend_runtime",
    "lib.backends.video_backends",
}
assert forbidden.isdisjoint(sys.modules), forbidden & sys.modules.keys()
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, f"视频契约首位导入加载了运行依赖：\n{result.stderr}"
