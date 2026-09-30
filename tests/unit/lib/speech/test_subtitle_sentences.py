"""Sentence splitting and reading-unit weighting for subtitle drafts."""

from __future__ import annotations

import pytest

from lib.speech.subtitle_sentences import split_sentences, subtitle_reading_units


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("他来了。她走了！你信吗？", ("他来了。", "她走了！", "你信吗？")),
        ("等等……真的吗？", ("等等……", "真的吗？")),
        ("……真的吗？", ("……真的吗？",)),
        ("什么？！走吧。", ("什么？！", "走吧。")),
        ("他说：“走吧。”然后离开。", ("他说：“走吧。”", "然后离开。")),
        ("没有句末标点", ("没有句末标点",)),
        ("第一段\n\n第二段", ("第一段\n\n第二段",)),
        ("第一句。\n第二句。", ("第一句。", "第二句。")),
        ("他问：“真的吗？”……", ("他问：“真的吗？”……",)),
        ("他问：“真的吗？”……然后走了。", ("他问：“真的吗？”", "……然后走了。")),
    ],
)
def test_chinese_splits_after_sentence_terminators_and_keeps_them(text: str, expected: tuple[str, ...]) -> None:
    assert split_sentences(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("It rained. Then it stopped! Really?", ("It rained.", "Then it stopped!", "Really?")),
        ("Wait... what?", ("Wait...", "what?")),
        ("Version 3.14 is out.", ("Version 3.14 is out.",)),
        ("Không sao. Đi thôi!", ("Không sao.", "Đi thôi!")),
        ("No terminator", ("No terminator",)),
        ("Line one.\nLine two.", ("Line one.", "Line two.")),
    ],
)
def test_latin_splits_only_at_terminators_followed_by_whitespace(text: str, expected: tuple[str, ...]) -> None:
    assert split_sentences(text) == expected


def test_abbreviations_may_be_cut_early() -> None:
    assert split_sentences("Dr. Smith left.") == ("Dr.", "Smith left.")


def test_foreign_words_inside_chinese_text_follow_the_chinese_rule() -> None:
    assert split_sentences("他用 iPhone. 拍了照。然后说 OK! 走吧。") == ("他用 iPhone. 拍了照。", "然后说 OK! 走吧。")


@pytest.mark.parametrize(
    ("text", "units"),
    [
        ("你好吗", 3),
        ("Hello brave new world.", 4),
        ("Không sao đâu.", 3),
        ("他用 iPhone 拍照。", 5),
        ("!!!", 1),
    ],
)
def test_reading_units_count_characters_for_chinese_and_words_otherwise(text: str, units: int) -> None:
    assert subtitle_reading_units(text) == units
