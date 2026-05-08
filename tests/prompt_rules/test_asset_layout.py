import pytest

from lib.prompt_rules.asset_layout import layout_for


def test_character_layout() -> None:
    text = layout_for("character")
    assert "三视图" not in text  # 我们用更具体的描述
    assert "正面" in text
    assert "侧面" in text


def test_scene_layout() -> None:
    text = layout_for("scene")
    assert "主画面" in text
    assert "细节" in text


def test_prop_layout() -> None:
    text = layout_for("prop")
    assert "正面" in text
    assert "45 度" in text
    assert "细节" in text


def test_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown asset_type"):
        layout_for("unknown")
