"""一集原文的首句与尾句，供创作者核对集的起止（分集规划摘要与「分集」视图共用）。"""

from __future__ import annotations

import re

#: 首句 / 尾句的最大字符数（含省略号）：超长时首句留开头、尾句留结尾。
EDGE_SENTENCE_MAX_CHARS = 60

# 一句 = 一行内到句末标点（连同其后的收尾引号 / 括号）为止；英文句点在收尾符号后是空白或行尾时算句末，
# 但全大写缩写（场景标题的 INT. / EXT.）后的句点不算，场景标题整行成句
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
