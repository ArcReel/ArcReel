"""Lightweight reading-unit metrics based on the source language.

`count_reading_units` counts semantic, language-specific reading units, matching
the user's notion of N characters per episode. This differs from language-neutral
character offsets used for locating text.

Input contract:
- Callers supply NFC-normalized text. With NFD/combining accents in languages such
  as Vietnamese, `\\w` word boundaries split combining marks out of tokens, so
  ``Hôm`` (``H + o + ̂ + m``) can count as multiple tokens. Normalize with
  ``unicodedata.normalize("NFC", text)`` at the file-reading boundary. This module
  does not normalize: string operations and offsets retain the input coordinates.

Design constraints:
- No internal lib/ dependencies, so agent skill scripts can import this module
  after adding its path to sys.path.
- Keep the interface stable: add branches for new languages without breaking callers.
"""

from __future__ import annotations

import re

# zh: CJK Unified Ideographs (basic block + Extension A), compatibility ideographs,
# CJK symbols/punctuation, fullwidth forms, and SIP CJK Extensions B-H
# (rare/historical characters and uncommon personal/place names, U+20000-U+323AF).
_ZH_UNIT_PATTERN = re.compile("[㐀-鿿豈-﫿　-〿＀-￯𠀀-𲎯]")

# Word-counted languages such as en / vi / ru use Unicode word boundaries
# (\w includes Latin and Cyrillic letters under re.UNICODE).
_LATIN_WORD_PATTERN = re.compile(r"\b\w+\b", re.UNICODE)


def _pattern_for(language: str | None) -> re.Pattern[str]:
    code = (language or "").strip().lower()
    if code in ("en", "vi", "ru"):
        return _LATIN_WORD_PATTERN
    return _ZH_UNIT_PATTERN


def count_reading_units(text: str, language: str | None) -> int:
    """Count reading units in the source language.

    zh: Chinese characters plus CJK punctuation / fullwidth symbols.
    en / vi / ru: Unicode word-boundary words. Numbers count as words; contractions
        such as "don't" split into ``don`` + ``t``. Counting a contraction as one
        token would require a different regular expression.
    Unknown / None / empty language: use zh for compatibility with older projects
        that have no source_language.
    """
    if not text:
        return 0
    return len(_pattern_for(language).findall(text))


def reading_unit_noun(language: str | None) -> str:
    """Return the Chinese unit noun: 词 for word-counted languages, otherwise 字.

    Derive the noun from ``_pattern_for``, the same classification used by
    ``count_reading_units``. Adding a word-counted language to ``_pattern_for`` automatically
    updates this function, avoiding per-caller word/character mappings that drift
    from the counter. Prompts use this noun for units per second or per episode.
    """
    return "词" if _pattern_for(language) is _LATIN_WORD_PATTERN else "字"


def find_reading_unit_offset(text: str, target_units: int, language: str | None) -> int:
    """Return the character offset just after reading unit ``target_units``.

    Use the same units as ``count_reading_units`` and scan in source order. A
    global proportion would map units to incorrect positions when ASCII/digits
    are unevenly distributed, including en/vi/ru or mixed Chinese text.

    - Return 0 for ``target_units <= 0`` or empty ``text``.
    - Return ``len(text)`` if there are fewer units than requested.
    """
    if target_units <= 0 or not text:
        return 0
    for count, match in enumerate(_pattern_for(language).finditer(text), start=1):
        if count >= target_units:
            return match.end()
    return len(text)
