"""Sandbox 端到端集成测试。

仅在 sandbox 工具可用的环境跑（macOS / Linux + bwrap）。
依赖：项目根有 `projects/_e2e_dummy/` 目录与合法 `project.json`。
"""

from __future__ import annotations

import shutil
import sys

import pytest

pytestmark = pytest.mark.skipif(
    not (sys.platform == "darwin" and shutil.which("sandbox-exec"))
    and not (sys.platform == "linux" and shutil.which("bwrap")),
    reason="sandbox tool not available on this runner",
)


@pytest.mark.asyncio
async def test_bash_ls_in_cwd_succeeds() -> None:
    """场景：agent 在项目 cwd 跑 `ls` 应成功放行。对齐 PoC #3。"""
    pytest.skip("作为 PoC #3 手动验收项；自动化在 CI sandbox runner 就绪后引入")


@pytest.mark.asyncio
async def test_bash_cat_env_denied() -> None:
    """场景：cat /app/.env 被 sandbox deny rule 拒。对齐 PoC #2。"""
    pytest.skip("作为 PoC #2 手动验收项")


@pytest.mark.asyncio
async def test_bash_curl_external_succeeds() -> None:
    """场景：curl 任意域名应放行。对齐 PoC #4。"""
    pytest.skip("作为 PoC #4 手动验收项")


@pytest.mark.asyncio
async def test_sdk_read_other_project_denied() -> None:
    """场景：SDK Read 跨项目读取被 hook 拒。"""
    pytest.skip("hook 单元测试已覆盖；e2e 选做")


@pytest.mark.asyncio
async def test_sdk_write_code_extension_denied() -> None:
    """场景：SDK Write 写 .py 进项目被 hook 拒。"""
    pytest.skip("hook 单元测试已覆盖；e2e 选做")
