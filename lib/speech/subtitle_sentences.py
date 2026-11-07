"""Pure sentence splitting and reading-unit weighting for subtitle drafts.

Text that contains CJK characters follows the Chinese rule (cut after ``。！？…``),
including any foreign words embedded in it; all other text follows the Latin rule
(cut at ``.!?`` followed by whitespace, so abbreviations may be cut early).
"""

from __future__ import annotations

import re

from lib.infra.text_metrics import count_reading_units

_ZH_TERMINATORS = "。！？…"
_ZH_CLOSERS = "”’」』）】》)"
_ZH_SENTENCE = re.compile(
    rf"[{_ZH_TERMINATORS}]*[^{_ZH_TERMINATORS}]+[{_ZH_TERMINATORS}]*[{_ZH_CLOSERS}]*",
)
_LATIN_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _is_chinese(text: str) -> bool:
    return count_reading_units(text, "zh") > 0


def split_sentences(text: str) -> tuple[str, ...]:
    """Split ``text`` into trimmed sentences that keep their closing punctuation."""

    if _is_chinese(text):
        matches = list(_ZH_SENTENCE.finditer(text))
        pieces = [match.group() for match in matches]
        if matches:
            # Only terminal punctuation immediately after a closing symbol at the end of the text is unmatched; append it to the preceding sentence.
            pieces[-1] += text[matches[-1].end() :]
    else:
        pieces = _LATIN_BOUNDARY.split(text)
    sentences = tuple(piece.strip() for piece in pieces if piece.strip())
    return sentences or (text.strip(),)


def subtitle_reading_units(text: str) -> int:
    """Return the timing weight of one sentence: Chinese counts characters, others count words."""

    language = "zh" if _is_chinese(text) else "en"
    return max(count_reading_units(text, language), 1)
