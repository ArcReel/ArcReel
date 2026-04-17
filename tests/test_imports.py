"""Import smoke test — catches circular deps and import-time side effects.

参数化遍历 lib/ 与 server/ 下核心子模块，每个 importlib.import_module 一次。
任何循环依赖、缺失依赖、顶层副作用崩溃都会在此红。
"""

from __future__ import annotations

import importlib

import pytest

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
    "lib.usage_tracker",
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


@pytest.mark.unit
@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports_cleanly(module_name: str) -> None:
    importlib.import_module(module_name)
