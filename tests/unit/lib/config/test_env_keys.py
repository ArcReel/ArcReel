"""env_keys set-invariant tests."""

from __future__ import annotations

from lib.config.env_keys import (
    ANTHROPIC_ENV_KEYS,
    OTHER_PROVIDER_ENV_KEYS,
    PROVIDER_SECRET_KEYS,
    is_provider_env_key,
)


def test_provider_secret_keys_is_subset_of_all_provider_keys():
    """Secret keys must belong to the other-provider-env union, preventing omissions."""
    for k in PROVIDER_SECRET_KEYS:
        if k == "ANTHROPIC_API_KEY":
            assert k in ANTHROPIC_ENV_KEYS
        else:
            assert k in OTHER_PROVIDER_ENV_KEYS, f"密钥 {k} 必须出现在 OTHER_PROVIDER_ENV_KEYS 中"


def test_openai_api_key_in_secret_lists():
    """Built-in OpenAI SDK falls back to OPENAI_API_KEY without api_key, requiring fail-fast coverage.
    Explicitly pin OPENAI_API_KEY: accidental removal from PROVIDER_SECRET_KEYS must fail here,
    whereas parametrized startup tests would silently shrink. The subset invariant above covers
    membership in OTHER_PROVIDER_ENV_KEYS."""
    assert "OPENAI_API_KEY" in PROVIDER_SECRET_KEYS


def test_openai_nonsecret_env_fallbacks_in_override_list():
    """OpenAI SDK nonsecret fallback env knobs (base_url / org / project / custom headers) stay outside
    fail-fast secret lists but all belong to SDK-subprocess environment overrides. Their nonsecret
    names evade _SECRET_ENV_NAME_PATTERNS, so only static lists strip them."""
    for key in ("OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_CUSTOM_HEADERS"):
        assert key in OTHER_PROVIDER_ENV_KEYS, f"{key} 必须出现在 OTHER_PROVIDER_ENV_KEYS 中"
        assert key not in PROVIDER_SECRET_KEYS, f"{key} 非密钥，不应进 fail-fast 名单"


def test_anthropic_keys_complete():
    """ANTHROPIC_ENV_KEYS covers every SDK-subprocess ANTHROPIC_* + CLAUDE_CODE_* input."""
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


def test_claude_auth_tokens_are_isolated_with_correct_secret_scope():
    """Isolate Claude alternative-auth tokens; only Anthropic tokens trigger startup rejection."""
    for key in ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        assert key in OTHER_PROVIDER_ENV_KEYS
        assert is_provider_env_key(key)

    assert "ANTHROPIC_AUTH_TOKEN" in PROVIDER_SECRET_KEYS
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in PROVIDER_SECRET_KEYS
