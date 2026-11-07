"""Preset provider-catalog unit tests."""

import pytest

from lib.agent.agent_provider_catalog import (
    CUSTOM_SENTINEL_ID,
    get_preset,
    list_presets,
)


def test_custom_sentinel_value() -> None:
    assert CUSTOM_SENTINEL_ID == "__custom__"


def test_anthropic_official_present() -> None:
    p = get_preset("anthropic-official")
    assert p is not None
    assert p.messages_url == "https://api.anthropic.com"
    assert p.discovery_url == "https://api.anthropic.com"
    assert p.icon_key == "Anthropic"
    # User table explicitly leaves official Anthropic default models empty and unrecommended.
    assert p.default_model == ""
    assert p.is_recommended is False


def test_arcreel_is_only_recommended() -> None:
    """ArcReel is the only recommended preset in the user table; all others are unrecommended."""
    recommended = [p for p in list_presets() if p.is_recommended]
    assert [p.id for p in recommended] == ["arcreel"]


def test_get_preset_unknown_returns_none() -> None:
    assert get_preset("does-not-exist") is None


def test_anthropic_official_first_arcreel_second() -> None:
    """Display order: official first, ArcReel API second."""
    presets = list_presets()
    assert presets[0].id == "anthropic-official"
    assert presets[1].id == "arcreel"


def test_no_duplicate_ids() -> None:
    ids = [p.id for p in list_presets()]
    assert len(ids) == len(set(ids))


def test_messages_url_https_only() -> None:
    for p in list_presets():
        assert p.messages_url.startswith("https://"), f"{p.id} messages_url not https"
        if p.discovery_url is not None:
            assert p.discovery_url.startswith("https://"), f"{p.id} discovery_url not https"


def test_curated_preset_set() -> None:
    """Catalog matches the supplied table: 11 presets."""
    expected = {
        "anthropic-official",
        "arcreel",
        "glm-cn",
        "glm-intl",
        "xiaomi-mimo",
        "deepseek",
        "minimax-cn",
        "minimax-intl",
        "kimi",
        "ark-coding-plan",
        "ark-agent-plan",
    }
    actual = {p.id for p in list_presets()}
    assert actual == expected


def test_default_models_match_table() -> None:
    """Default models specified by the user table."""
    expected = {
        "anthropic-official": "",
        "arcreel": "gpt-5.5",
        "glm-cn": "glm-5.1",
        "glm-intl": "glm-5.1",
        "xiaomi-mimo": "mimo-v2.5-pro",
        "deepseek": "deepseek-v4-pro",
        "minimax-cn": "MiniMax-M3",
        "minimax-intl": "MiniMax-M3",
        "kimi": "",
        "ark-coding-plan": "doubao-seed-evolving",
        "ark-agent-plan": "doubao-seed-evolving",
    }
    actual = {p.id: p.default_model for p in list_presets()}
    assert actual == expected


def test_api_key_url_required() -> None:
    """Every preset requires a Get API Key link for user navigation."""
    for p in list_presets():
        assert p.api_key_url, f"{p.id} missing api_key_url"
        assert p.api_key_url.startswith("https://"), f"{p.id} api_key_url not https"


def test_preset_dataclass_is_frozen() -> None:
    import dataclasses

    p = get_preset("anthropic-official")
    assert p is not None
    assert dataclasses.is_dataclass(p)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.display_name = "x"


def test_ark_presets_carry_suggested_models_without_discovery() -> None:
    """Both Ark subscription tiers lack discovery APIs; disable discovery and use suggested models."""
    for preset_id in ("ark-coding-plan", "ark-agent-plan"):
        p = get_preset(preset_id)
        assert p is not None
        assert p.discovery_url is None, f"{preset_id} 不应声明 discovery_url"
        assert p.default_model in p.suggested_models
        assert len(p.suggested_models) >= 2
        assert p.notes_i18n_key is not None
