import pytest

from lib.prompt_rules.episode_pacing import (
    DRAMA_PACING_RULES,
    NARRATION_PACING_RULES,
    render_pacing_section,
)


def test_drama_rules_keywords() -> None:
    text = render_pacing_section("drama")
    assert text == DRAMA_PACING_RULES
    assert "4 秒" in text
    assert "钩子" in text
    assert "15 秒" in text
    assert "Close-up" in text


def test_narration_rules_keywords() -> None:
    text = render_pacing_section("narration")
    assert text == NARRATION_PACING_RULES
    assert "4 秒" in text
    assert "钩子" in text
    assert "卡点留悬" in text


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="unknown content_mode"):
        render_pacing_section("unknown")


def test_softened_phrasing() -> None:
    """文案应使用柔性建议词（"宜 / 例 / 建议"）而非硬性"必须"。"""
    drama = render_pacing_section("drama")
    # 不应再用"铁则"措辞
    assert "铁则" not in drama
    assert "杜绝" not in drama
    assert "禁止" not in drama
    # 至少应包含"建议 / 宜"等柔性词或"例"
    assert any(w in drama for w in ["建议", "宜", "例"])
