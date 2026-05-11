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
    assert p.is_recommended


def test_get_preset_unknown_returns_none() -> None:
    assert get_preset("does-not-exist") is None


def test_list_presets_recommended_first() -> None:
    presets = list_presets()
    # 第一个必须是推荐项
    assert presets[0].is_recommended


def test_no_duplicate_ids() -> None:
    ids = [p.id for p in list_presets()]
    assert len(ids) == len(set(ids))


def test_messages_url_https_only() -> None:
    for p in list_presets():
        assert p.messages_url.startswith("https://"), f"{p.id} messages_url not https"
        if p.discovery_url is not None:
            assert p.discovery_url.startswith("https://"), f"{p.id} discovery_url not https"


def test_first_batch_required_presets() -> None:
    """第一批 catalog 必须覆盖 spec §1.2 表格中的网关。"""
    required = {
        "anthropic-official",
        "deepseek",
        "kimi",
        "glm",
        "minimax-intl",
        "minimax-cn",
        "hunyuan",
        "lkeap",
        "ark-coding",
        "bailian",
        "xiaomi-mimo",
    }
    actual = {p.id for p in list_presets()}
    missing = required - actual
    assert not missing, f"缺失预设: {missing}"


def test_preset_dataclass_is_frozen() -> None:
    p = get_preset("anthropic-official")
    assert p is not None
    import dataclasses

    assert dataclasses.is_dataclass(p)
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        p.display_name = "x"  # type: ignore[misc]
