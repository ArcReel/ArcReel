"""sync_anthropic_env 主路径测试 — active credential 优先，否则 fallback 旧 settings。"""

from __future__ import annotations

import os

import pytest

from lib.config.service import sync_anthropic_env
from lib.db.models.config import SystemSetting
from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)


@pytest.fixture(autouse=True)
def _clear_env():
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    yield
    for k in _ENV_KEYS:
        os.environ.pop(k, None)


@pytest.mark.asyncio
async def test_sync_uses_active_credential(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    cred = await repo.create(
        preset_id="deepseek",
        display_name="ds",
        base_url="https://api.deepseek.com/anthropic",
        api_key="sk-x",
        model="deepseek-chat",
    )
    await async_session.flush()
    await repo.set_active(cred.id)
    await async_session.flush()

    await sync_anthropic_env(async_session)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-x"
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert os.environ["ANTHROPIC_MODEL"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_sync_fallback_to_system_settings(async_session) -> None:
    """没有 active credential 时，回退到 system_settings 旧 keys。"""
    async_session.add(SystemSetting(key="anthropic_api_key", value="legacy-k"))
    async_session.add(SystemSetting(key="anthropic_base_url", value="https://legacy.example/"))
    async_session.add(SystemSetting(key="anthropic_model", value="legacy-model"))
    await async_session.flush()

    await sync_anthropic_env(async_session)
    assert os.environ["ANTHROPIC_API_KEY"] == "legacy-k"
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://legacy.example/"
    assert os.environ["ANTHROPIC_MODEL"] == "legacy-model"


@pytest.mark.asyncio
async def test_sync_no_credential_no_settings_clears_env(async_session) -> None:
    os.environ["ANTHROPIC_API_KEY"] = "stale"
    await sync_anthropic_env(async_session)
    assert "ANTHROPIC_API_KEY" not in os.environ
