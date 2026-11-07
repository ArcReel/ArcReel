"""Episode source opening/closing sentences for creator boundary verification, shared by planning summaries/Episodes views."""

from __future__ import annotations

import re

#: Edge-sentence character maximum including ellipses; retain first beginnings/last endings.
EDGE_SENTENCE_MAX_CHARS = 60

# Sentences end at in-line terminal punctuation plus closing quotes/brackets; English periods terminate when closers precede whitespace/end-of-line,
# except uppercase abbreviations INT./EXT., whose scene-heading lines form whole sentences.
_SENTENCE_RE = re.compile(
    r"[^\n]+?(?:[。！？!?…]+[」』”’\"'）)]*|(?<![A-Z]{2})\.[」』”’\"'）)]*(?=\s|$)|$)", re.MULTILINE
)


def edge_sentences(segment: str) -> tuple[str, str]:
    """Opening/closing sentences; when long, retain the opening beginning and closing ending, marking truncation with ellipses. Both empty without sentences."""
    sentences = [m.group().strip() for m in _SENTENCE_RE.finditer(segment)]
    sentences = [sentence for sentence in sentences if sentence]
    if not sentences:
        return "", ""
    first, last = sentences[0], sentences[-1]
    limit = EDGE_SENTENCE_MAX_CHARS
    if len(first) > limit:
        first = first[: limit - 1] + "…"
    if len(last) > limit:
        last = "…" + last[-(limit - 1) :]
    return first, last


__all__ = ["EDGE_SENTENCE_MAX_CHARS", "edge_sentences"]
