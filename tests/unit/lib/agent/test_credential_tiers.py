"""Direct OpenAI routing never inherits legacy Anthropic models."""

import pytest

from lib.agent.credential_tiers import TIER_SETTING_KEYS, TIERS, tier_models
from lib.db.models.agent_credential import AgentAnthropicCredential


@pytest.mark.parametrize("preset_id", ["openai-official", "__custom__"])
@pytest.mark.parametrize("model", [None, "gpt-6-luna"])
def test_direct_openai_tiers_ignore_legacy_settings(preset_id: str, model: str | None) -> None:
    credential = AgentAnthropicCredential(
        preset_id=preset_id,
        base_url="https://api.openai.com/v1",
        model=model,
    )
    settings = dict.fromkeys(TIER_SETTING_KEYS.values(), "claude-legacy")

    assert tier_models(credential, settings) == dict.fromkeys(TIERS, model or "gpt-6.1-sol")


def test_direct_openai_explicit_tier_overrides_are_preserved() -> None:
    credential = AgentAnthropicCredential(
        preset_id="openai-official",
        base_url="https://api.openai.com",
        model="gpt-6.1-sol",
        haiku_model="gpt-6-luna",
        subagent_model="gpt-6-astra",
    )

    assert tier_models(credential, {"anthropic_default_sonnet_model": "claude-legacy"}) == {
        "model": "gpt-6.1-sol",
        "haiku": "gpt-6-luna",
        "sonnet": "gpt-6.1-sol",
        "opus": "gpt-6.1-sol",
        "subagent": "gpt-6-astra",
    }


def test_anthropic_compatible_tiers_keep_legacy_settings() -> None:
    credential = AgentAnthropicCredential(preset_id="__custom__", base_url="https://gateway.example.test", model="main")

    assert tier_models(credential, {"anthropic_default_haiku_model": "small"}) == {
        "model": "main",
        "haiku": "small",
        "sonnet": "main",
        "opus": "main",
        "subagent": "main",
    }
