"""env_keys 模块集合不变量测试。"""

from __future__ import annotations

from lib.config.env_keys import (
    ANTHROPIC_ENV_KEYS,
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


def test_openai_api_key_in_secret_lists():
    """OpenAI 是内置 provider，其 SDK 在 api_key 缺省时回落读 OPENAI_API_KEY，
    因此该密钥必须进入 fail-fast 名单。显式 pin OPENAI_API_KEY 作回归守卫：
    若它被从 PROVIDER_SECRET_KEYS 误删，此断言会直接失败，而参数化的启动断言
    测试只会静默缩小覆盖。其在 OTHER_PROVIDER_ENV_KEYS 的存在由上方子集不变量保证。"""
    assert "OPENAI_API_KEY" in PROVIDER_SECRET_KEYS


def test_openai_nonsecret_env_fallbacks_in_override_list():
    """OpenAI SDK 在 client 未显式传值时会回落读的非密钥 env 旋钮（base_url /
    org / project / custom headers），均不进 fail-fast 密钥名单，但须整组纳入
    SDK 子进程 env 覆盖名单——它们非密钥命名，_SECRET_ENV_NAME_PATTERNS 兜不到，
    只能靠静态名单剥离。"""
    for key in ("OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_CUSTOM_HEADERS"):
        assert key in OTHER_PROVIDER_ENV_KEYS, f"{key} 必须出现在 OTHER_PROVIDER_ENV_KEYS 中"
        assert key not in PROVIDER_SECRET_KEYS, f"{key} 非密钥，不应进 fail-fast 名单"


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
