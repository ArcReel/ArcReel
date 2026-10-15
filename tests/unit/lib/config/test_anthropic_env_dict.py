"""build_anthropic_env 行为测试 — 只读 DB、返回环境变量与计价依据快照、不写 environ。"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

from lib.agent.credential_tiers import AgentPricing
from lib.config.service import build_anthropic_env


@pytest.mark.asyncio
async def test_active_credential_returns_full_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    repo_mock = AsyncMock()
    cred = type(
        "Cred",
        (),
        {
            "id": 7,
            "preset_id": "anthropic-official",
            "api_key": "sk-test",
            "base_url": "https://api.anthropic.com",
            "model": "claude-opus-4-7",
            "haiku_model": "claude-haiku-4-5",
            "sonnet_model": "claude-sonnet-4-6",
            "opus_model": "claude-opus-4-7",
            "subagent_model": "claude-haiku-4-5",
            "multiplier": 0.8,
            "tier_bindings": None,
        },
    )()
    repo_mock.get_active = AsyncMock(return_value=cred)

    setting_repo = AsyncMock()
    setting_repo.get_all = AsyncMock(return_value={})

    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )
    monkeypatch.setattr("lib.config.service.SystemSettingRepository", lambda _s: setting_repo)

    built = await build_anthropic_env(session)
    result = built.env
    assert built.pricing == AgentPricing(credential_id=7, multiplier=0.8)
    assert result["ANTHROPIC_API_KEY"] == "sk-test"
    assert result["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"
    assert result["ANTHROPIC_MODEL"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_no_active_credential_returns_empty_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    repo_mock = AsyncMock()
    repo_mock.get_active = AsyncMock(return_value=None)

    setting_repo = AsyncMock()
    setting_repo.get_all = AsyncMock(return_value={})

    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )
    monkeypatch.setattr("lib.config.service.SystemSettingRepository", lambda _s: setting_repo)

    built = await build_anthropic_env(session)
    assert built.pricing == AgentPricing()
    assert built.env["ANTHROPIC_API_KEY"] == ""
    assert built.env["ANTHROPIC_BASE_URL"] == ""


@pytest.mark.asyncio
async def test_no_active_credential_falls_back_to_system_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """双轨期兼容：无 active credential 时从 system_settings legacy key 读取。"""
    session = AsyncMock()
    repo_mock = AsyncMock()
    repo_mock.get_active = AsyncMock(return_value=None)

    legacy = {
        "anthropic_api_key": "legacy-sk",
        "anthropic_base_url": "https://legacy.anthropic.com",
        "anthropic_model": "claude-legacy-model",
    }
    setting_repo = AsyncMock()
    setting_repo.get_all = AsyncMock(return_value=legacy)

    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )
    monkeypatch.setattr("lib.config.service.SystemSettingRepository", lambda _s: setting_repo)

    result = (await build_anthropic_env(session)).env
    assert result["ANTHROPIC_API_KEY"] == "legacy-sk"
    assert result["ANTHROPIC_BASE_URL"] == "https://legacy.anthropic.com"
    assert result["ANTHROPIC_MODEL"] == "claude-legacy-model"
    # 未在 settings 中的 key 仍返回空串
    assert result["CLAUDE_CODE_SUBAGENT_MODEL"] == ""


@pytest.mark.asyncio
async def test_function_does_not_touch_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """spec §6.3 红线：build 函数不能写 os.environ。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    baseline = dict(os.environ)

    session = AsyncMock()
    repo_mock = AsyncMock()
    cred = type(
        "Cred",
        (),
        {
            "id": 7,
            "preset_id": "__custom__",
            "api_key": "sk-test",
            "base_url": "x",
            "model": "y",
            "haiku_model": None,
            "sonnet_model": None,
            "opus_model": None,
            "subagent_model": None,
            "multiplier": 1.0,
            "tier_bindings": None,
        },
    )()
    repo_mock.get_active = AsyncMock(return_value=cred)

    setting_repo = AsyncMock()
    setting_repo.get_all = AsyncMock(return_value={})

    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )
    monkeypatch.setattr("lib.config.service.SystemSettingRepository", lambda _s: setting_repo)

    await build_anthropic_env(session)
    assert dict(os.environ) == baseline, "build 函数禁止改 os.environ"


def _patch_cred(monkeypatch: pytest.MonkeyPatch, **fields: object) -> None:
    base: dict[str, object] = {
        "id": 7,
        "api_key": "sk",
        "base_url": "https://gw.example.com",
        "model": "",
        "haiku_model": "",
        "sonnet_model": "",
        "opus_model": "",
        "subagent_model": "",
        "preset_id": "deepseek",
        "multiplier": 1.0,
        "tier_bindings": None,
    }
    base.update(fields)
    cred = type("Cred", (), base)()
    repo_mock = AsyncMock()
    repo_mock.get_active = AsyncMock(return_value=cred)
    setting_repo = AsyncMock()
    setting_repo.get_all = AsyncMock(return_value={})
    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )
    monkeypatch.setattr("lib.config.service.SystemSettingRepository", lambda _s: setting_repo)


@pytest.mark.asyncio
async def test_non_official_preset_fills_blank_tiers_with_main_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cred(monkeypatch, model="deepseek-v4", sonnet_model="deepseek-v4-pro")
    result = (await build_anthropic_env(AsyncMock())).env
    assert result["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek-v4"
    assert result["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "deepseek-v4-pro"
    assert result["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "deepseek-v4"
    assert result["CLAUDE_CODE_SUBAGENT_MODEL"] == "deepseek-v4"


@pytest.mark.asyncio
async def test_official_preset_leaves_blank_tiers_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cred(monkeypatch, preset_id="anthropic-official", model="claude-opus-4-7", sonnet_model="claude-sonnet-4-6")
    result = (await build_anthropic_env(AsyncMock())).env
    assert result["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == ""
    assert result["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "claude-sonnet-4-6"
    assert result["ANTHROPIC_DEFAULT_OPUS_MODEL"] == ""
    assert result["CLAUDE_CODE_SUBAGENT_MODEL"] == ""


@pytest.mark.asyncio
async def test_pricing_snapshot_takes_the_multiplier_and_the_bindings_of_the_tiers_from_the_same_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """计价依据与环境变量取自同一行：倍率，以及带绑定的档位实际调用的模型名（留空档位按填充后的名字）。"""
    _patch_cred(
        monkeypatch,
        model="gw-main",
        haiku_model="GW/Fast.1",
        multiplier=0.5,
        tier_bindings={"haiku": "acme/fast-1", "opus": "acme/big", "sonnet": ""},
    )
    pricing = (await build_anthropic_env(AsyncMock())).pricing

    assert pricing.credential_id == 7
    assert pricing.multiplier == 0.5
    # 模型名比较忽略大小写与命名空间；留空的 opus 档位填成主模型，主模型档位自己没有绑定时取 opus 的绑定
    assert pricing.entry_binding_for("gw/fast-1") == "acme/fast-1"
    assert pricing.entry_binding_for("gw-main") == "acme/big"
    assert pricing.entry_binding_for("other") is None
