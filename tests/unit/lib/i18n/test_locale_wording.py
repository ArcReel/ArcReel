"""zh punctuation follows the same rules as frontend/src/i18n/locale-wording.test.ts."""

import re

import pytest

from lib.i18n import MESSAGES

# Matches frontend \p{Script=Han}: basic, Extension A, compatibility, and Extension B onward.
HAN = r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f]"

ZH_RULES = [
    ("中文后用全角冒号", re.compile(rf"{HAN}\s?:")),
    ("省略号用「…」", re.compile(r"\.\.\.|……")),
    # Check only paired quotation marks; isolated " in invalid-character enumerations denotes the character itself.
    ("引号用「」", re.compile(r'"[^"]*"')),
    # Ranges require placeholders, percentages, or trailing units; two bare numeric endpoints are literal user-input syntax.
    (
        "数值范围用「～」",
        re.compile(
            rf"\{{\w+\}}\s?[-–]\s?(\d|\{{)|\d\s?[-–]\s?\{{|\d%\s?[-–]\s?\d|\d\s?[-–]\s?\d+(\.\d+)?(%|\s?({HAN}|[A-Za-z]))"
        ),
    ),
]


@pytest.mark.parametrize(("rule", "pattern"), ZH_RULES, ids=[r for r, _ in ZH_RULES])
def test_zh_messages_follow_punctuation_rule(rule, pattern):
    offenders = {key: text for key, text in MESSAGES["zh"].items() if pattern.search(text)}
    assert offenders == {}, rule
