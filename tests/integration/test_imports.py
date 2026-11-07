"""Import smoke test — catches circular deps and import-time side effects.

Parameterize core lib/ and server/ submodules, calling importlib.import_module once each.
Circular/missing dependencies and top-level side-effect crashes all fail here.

Same-process traversal has blind spots; :func:`test_module_imports_first_in_fresh_process` covers them
for the reasons in that function.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Core-submodule allowlist: append new packages here, avoiding pkgutil.walk_packages
# to prevent accidentally loading lib.i18n.zh/en translation packages and alembic.versions migration scripts.
# Modules with module-level mutual references (lib.config ↔ lib.custom_provider; endpoint dispatchers ↔ kind implementations)
# and foundational modules that must remain lightweight also require FIRST_IMPORT_MODULES registration.
MODULES = [
    # Top-level single-file lib modules
    "lib.backends.ark_shared",
    "lib.backends.backend_runtime",
    "arcreel_market_core.video_backend_contract",
    "lib.project.asset_fingerprints",
    "lib.project.data_validator",
    "lib.backends.gemini_shared",
    "lib.generation.generation_queue",
    "lib.generation.generation_queue_client",
    "lib.generation.generation_worker",
    "lib.generation.restart_recovery",
    "lib.generation.task_failure_encoding",
    "lib.generation.video_resume",
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
    # lib subpackages
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
    "server.services.tasks.formal_image_commit",
]


# Modules that must import first successfully, verified individually in fresh interpreters (see case docstring).
FIRST_IMPORT_MODULES = [
    "lib.backends.backend_runtime",
    "lib.backends.image_backends.base",
    "arcreel_market_core.video_backend_contract",
    "lib.config.resolver",
    "lib.custom_provider.backends",
    "lib.custom_provider.capabilities",
    "lib.custom_provider.comfyui.comfyui_backend",
    "lib.custom_provider.comfyui.comfyui_client",
    "lib.custom_provider.comfyui.import_shapes",
    "lib.custom_provider.comfyui.inference",
    "lib.custom_provider.comfyui.request_builder",
    "arcreel_market_core.comfyui.validator",
    "lib.custom_provider.discovery",
    "arcreel_market_core.endpoint_definition",
    "lib.custom_provider.endpoint_test",
    "lib.custom_provider.endpoints",
    "lib.custom_provider.factory",
    "lib.custom_provider.loader",
    "server.services.currency.upload_finalize",
    "server.services.tasks.derivative_sheet_tasks",
    "server.services.tasks.formal_image_commit",
    "server.services.tasks.generation_tasks",
    "server.services.tasks.image_edit_tasks",
    "server.services.tasks.reference_video_tasks",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports_cleanly(module_name: str) -> None:
    """Smoke test: full-name imports succeed without import-time side-effect errors (configuration reads, circular imports)."""
    assert importlib.import_module(module_name).__name__ == module_name


@pytest.mark.parametrize("module_name", FIRST_IMPORT_MODULES)
def test_module_imports_first_in_fresh_process(module_name: str) -> None:
    """The module succeeds as the interpreter's first imported project module.

    ``lib.config`` and ``lib.custom_provider`` reference each other (the latter assembles backends that read
    the former's URL utilities). Endpoint dispatchers and ``kind`` implementations also cross-reference
    package boundaries (``endpoint_definition`` imports each kind's validators; ``comfyui.import_shapes``
    imports ``kinds`` constants back). Task entry ``generation_tasks`` registers executors at module scope;
    executors/upload finalization share formal-image commits and may depend only toward ``formal_image_commit``,
    never back toward the task entry. Moving any such edge to module-level import creates a cycle that fails
    only with certain first imports; same-process smoke tests miss it because previous cases warm ``sys.modules``.
    Fresh subprocesses alone lock down independent imports in arbitrary order.
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
        # Blocking import-time side effects (network/lock waits) are among the prohibited cases; timeouts must explicitly fail
        # rather than consume the CI job's entire time limit.
        pytest.fail(f"{module_name} 首位导入超时，疑似存在阻塞式顶层副作用")
    assert result.returncode == 0, f"{module_name} 无法作为首个导入：\n{result.stderr}"


def test_video_backend_contract_first_import_stays_light() -> None:
    """Importing video contracts first loads no runtime support, third-party HTTP/ORM, or video implementation packages."""
    code = """
import sys
import arcreel_market_core.video_backend_contract

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
