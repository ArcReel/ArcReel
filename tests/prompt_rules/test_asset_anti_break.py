import pytest

from lib.prompt_rules.asset_anti_break import (
    NEGATIVE_BASE,
    negative_for,
    positive_for,
)


def test_positive_per_type_distinct() -> None:
    char = positive_for("character")
    scene = positive_for("scene")
    prop = positive_for("prop")
    assert char and scene and prop
    assert char != scene != prop != char


def test_positive_keywords() -> None:
    assert "五指" in positive_for("character")
    assert "对称" in positive_for("character")
    assert "透视" in positive_for("scene")
    assert "焦点" in positive_for("prop")


def test_negative_keywords() -> None:
    text = negative_for("character")
    assert "畸形" in text
    assert "断指" in text
    assert "乱码" in text


def test_negative_same_for_all_types() -> None:
    assert negative_for("character") == NEGATIVE_BASE
    assert negative_for("scene") == NEGATIVE_BASE
    assert negative_for("prop") == NEGATIVE_BASE


def test_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown asset_type"):
        positive_for("unknown")
    with pytest.raises(ValueError, match="unknown asset_type"):
        negative_for("unknown")
