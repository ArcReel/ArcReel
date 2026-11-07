"""Bundled subtitle-font glyph preflight identifies unsupported characters through cmap."""

from __future__ import annotations

from lib.subtitle_style.font import missing_glyphs


def test_missing_glyphs_lists_each_uncovered_character_once_in_order() -> None:
    assert missing_glyphs("你好😀，Việt Nam 𠀀 再见😀") == "😀𠀀"


def test_whitespace_and_control_characters_never_count_as_missing() -> None:
    assert missing_glyphs("第一行\n第二行\t​结尾") == ""
