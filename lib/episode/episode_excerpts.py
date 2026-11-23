"""一集原文的首句与尾句，供创作者核对集的起止（分集规划摘要与「分集」视图共用）。"""

from __future__ import annotations

import re

EDGE_SENTENCE_MAX_CHARS = 60

_SENTENCE_RE = re.compile(
    r"[^\n]+?(?:[。！？!?…]+[」』”’\"'）)]*|(?<![A-Z]{2})\.[」』”’\"'）)]*(?=\s|$)|$)", re.MULTILINE
)


def edge_sentences(segment: str) -> tuple[str, str]:
    """原文的首句与尾句，超长时首句截留开头、尾句截留结尾并以省略号标出截断处；没有句子时两者都是空串。"""
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
