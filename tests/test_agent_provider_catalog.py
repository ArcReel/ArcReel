"""预设供应商目录单元测试。"""

from lib.agent_provider_catalog import (
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
    # 用户表格明确 Anthropic 官方默认模型为空、不标推荐
    assert p.default_model == ""
    assert p.is_recommended is False


def test_arcreel_is_only_recommended() -> None:
    """ArcReel 是用户表格中唯一标推荐的预设;其他全部不推荐."""
    recommended = [p for p in list_presets() if p.is_recommended]
    assert [p.id for p in recommended] == ["arcreel"]


def test_get_preset_unknown_returns_none() -> None:
    assert get_preset("does-not-exist") is None


def test_anthropic_official_first_arcreel_second() -> None:
    """显示顺序:官方第一,ArcReel API 第二."""
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
    """目录与用户提供的表格保持一致;9 条预设."""
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
    }
    actual = {p.id for p in list_presets()}
    assert actual == expected


def test_default_models_match_table() -> None:
    """用户表格指定的默认模型."""
    expected = {
        "anthropic-official": "",
        "arcreel": "gpt-5.5",
        "glm-cn": "glm-5.1",
        "glm-intl": "glm-5.1",
        "xiaomi-mimo": "mimo-v2.5-pro",
        "deepseek": "deepseek-v4-pro",
        "minimax-cn": "MiniMax-M2.7",
        "minimax-intl": "MiniMax-M2.7",
        "kimi": "",
    }
    actual = {p.id: p.default_model for p in list_presets()}
    assert actual == expected


def test_api_key_url_required() -> None:
    """每条预设都必须有「获取 API Key」链接(便于用户跳转)."""
    for p in list_presets():
        assert p.api_key_url, f"{p.id} missing api_key_url"
        assert p.api_key_url.startswith("https://"), f"{p.id} api_key_url not https"


def test_preset_dataclass_is_frozen() -> None:
    import dataclasses

    import pytest

    p = get_preset("anthropic-official")
    assert p is not None
    assert dataclasses.is_dataclass(p)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.display_name = "x"  # type: ignore[misc]
