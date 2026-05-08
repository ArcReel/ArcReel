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
    assert "定格卡点" in text
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
