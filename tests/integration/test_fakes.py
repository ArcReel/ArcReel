"""共享测试助手的隔离回归测试。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_use_project_manager_teardown_restores_unimported_consumers() -> None:
    """在下游模块尚未导入的干净进程中调用 use_project_manager，生效期全量生效、清理后全部恢复原始对象。"""
    code = """
import importlib
import sys
from pathlib import Path
import pytest
import lib.project.project_manager as pm_source
from tests.fakes import use_project_manager

real_get_pm = pm_source.get_project_manager

assert "server.routers.projects" not in sys.modules
assert "server.routers.scenes" not in sys.modules
assert "server.app" not in sys.modules

sentinel = object()
mp = pytest.MonkeyPatch()
use_project_manager(mp, sentinel)

fake_factory = pm_source.get_project_manager
assert fake_factory is not real_get_pm

import server.app
import server.routers.projects as projects_router
import server.routers.scenes as scenes_router

assert server.app is not None
assert pm_source.get_project_manager() is sentinel
assert projects_router.get_project_manager() is sentinel
assert scenes_router.get_project_manager() is sentinel

for root in ("lib", "server"):
    for path in sorted(Path(root).rglob("*.py")):
        if "get_project_manager" not in path.read_text(encoding="utf-8"):
            continue
        parts = path.with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        importlib.import_module(".".join(parts))

unpatched = []
for name, module in list(sys.modules.items()):
    if module is None or not name.startswith(("lib.", "server.")):
        continue
    for attr_name, value in list(vars(module).items()):
        if value is real_get_pm:
            unpatched.append(f"{name}.{attr_name}")

assert not unpatched, f"use_project_manager left real get_project_manager on: {sorted(set(unpatched))}"

mp.undo()

assert pm_source.get_project_manager is real_get_pm
assert projects_router.get_project_manager is real_get_pm
assert scenes_router.get_project_manager is real_get_pm

leaked = []
for name, module in list(sys.modules.items()):
    if module is None or not name.startswith(("lib.", "server.")):
        continue
    for attr_name, value in list(vars(module).items()):
        if value is fake_factory:
            leaked.append(f"{name}.{attr_name}")
    bound = getattr(module, "get_project_manager", None)
    if bound is not None and bound is not real_get_pm:
        leaked.append(f"{name}.get_project_manager")

assert not leaked, f"teardown left fake get_project_manager on: {sorted(set(leaked))}"
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, f"use_project_manager teardown leaked fake binding:\n{result.stderr}"
