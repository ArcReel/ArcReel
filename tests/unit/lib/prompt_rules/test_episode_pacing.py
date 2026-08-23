"""``render_pacing_section`` 只按 content_mode 交回对应常量，断言对照常量本身，不抄文案措辞。"""

import pytest

from lib.prompt_rules.episode_pacing import (
    DRAMA_PACING_RULES,
    NARRATION_PACING_RULES,
    render_pacing_section,
)


def test_drama_mode_renders_the_drama_constant() -> None:
    assert render_pacing_section("drama") == DRAMA_PACING_RULES


def test_narration_mode_renders_the_narration_constant() -> None:
    assert render_pacing_section("narration") == NARRATION_PACING_RULES


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="unknown content_mode"):
        render_pacing_section("unknown")
