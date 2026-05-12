"""env_keys 模块集合不变量测试。"""

from __future__ import annotations

from lib.config.env_keys import (
    ANTHROPIC_ENV_KEYS,
    AUTH_ALLOWED_KEYS,
    OTHER_PROVIDER_ENV_KEYS,
    PROVIDER_SECRET_KEYS,
)


def test_provider_secret_keys_is_subset_of_all_provider_keys():
    """密钥集合必须在「其他 provider env」的并集中（防漏列）。"""
    for k in PROVIDER_SECRET_KEYS:
        if k == "ANTHROPIC_API_KEY":
            assert k in ANTHROPIC_ENV_KEYS
        else:
            assert k in OTHER_PROVIDER_ENV_KEYS, f"密钥 {k} 必须出现在 OTHER_PROVIDER_ENV_KEYS 中"


def test_secret_keys_are_disjoint_from_auth_whitelist():
    """密钥不能出现在 AUTH 白名单 — 否则启动断言会冲突。"""
    overlap = PROVIDER_SECRET_KEYS & AUTH_ALLOWED_KEYS
    assert not overlap, f"密钥与 AUTH 白名单冲突: {overlap}"


def test_anthropic_keys_complete():
    """ANTHROPIC_ENV_KEYS 必须覆盖 SDK 子进程读取的全部 ANTHROPIC_* + CLAUDE_CODE_*。"""
    required = {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    }
    assert required <= set(ANTHROPIC_ENV_KEYS)
