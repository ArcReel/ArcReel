"""记忆索引 ``MEMORY.md`` 的加载口径：行数、字节数、超限判定与截断的单一实现。

两级记忆共用这一套口径。会话装配注入用户记忆索引时按它截断，记忆列表接口按它
报 ``line_count`` / ``byte_size`` / ``over_limit``——同一份索引因此在文件柜里显示的
``N/200`` 与 Agent 实际读到的内容一致，``over_limit`` 为真等价于「装配时会被截断」。

口径取自原生 auto memory（CLI 2.1.259：``MEMORY.md``、200 行、25 000）。行数与字节
都按 ``strip()`` 之后的正文算：首尾空行不进上下文，也就不该占额度。字节按 UTF-8 实际
字节，而非原生那处 JS 字符串 ``.length``（UTF-16 码元，中文一字算 1）——中文索引下
按 UTF-8 算更保守，也与「25 000 字节」的字面一致。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 保留的索引文件名：它是记忆的目录页。
INDEX_FILENAME = "MEMORY.md"

#: 索引的加载上限：装配时按此截断，列表接口按此标 ``over_limit``，写入不受拦截。
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25_000

#: 截断后追加的提示。Agent 看不到被丢掉的部分，只有提示能让它去整理索引，
#: 而不是以为索引就这么长。
_TRUNCATION_NOTICE = (
    "> 提示：`{filename}` 共 {line_count} 行 / {byte_size} 字节，"
    "超出 {max_lines} 行 / {max_bytes} 字节的加载上限，以上只是被加载的部分。"
    "每条索引保持一行、约 200 字符以内，细节写进主题文件。"
)


@dataclass(frozen=True)
class MemoryIndexStats:
    """一份索引正文的规模与超限判定。"""

    line_count: int
    byte_size: int

    @property
    def over_limit(self) -> bool:
        return self.line_count > INDEX_MAX_LINES or self.byte_size > INDEX_MAX_BYTES


def memory_index_stats(text: str) -> MemoryIndexStats:
    """索引正文的行数与 UTF-8 字节数；空白内容按零处理。"""
    trimmed = text.strip()
    if not trimmed:
        return MemoryIndexStats(line_count=0, byte_size=0)
    return MemoryIndexStats(line_count=trimmed.count("\n") + 1, byte_size=len(trimmed.encode("utf-8")))


def truncate_memory_index(text: str) -> str:
    """把索引正文截到加载上限内，超限时追加一行提示。

    先按行截到 200 行，再按 UTF-8 字节截到 25 000 以内的最后一个换行——两道都做，
    因为单行可以很长，只截行数护不住上下文。按字节切时 ``errors="ignore"`` 只丢掉
    被切开的那个码位，不会留下半个字符。
    """
    trimmed = text.strip()
    if not trimmed:
        return ""
    stats = memory_index_stats(trimmed)
    if not stats.over_limit:
        return trimmed

    kept = "\n".join(trimmed.split("\n")[:INDEX_MAX_LINES])
    encoded = kept.encode("utf-8")
    if len(encoded) > INDEX_MAX_BYTES:
        head = encoded[:INDEX_MAX_BYTES]
        newline_at = head.rfind(b"\n")
        head = head[:newline_at] if newline_at > 0 else head
        kept = head.decode("utf-8", errors="ignore")
    notice = _TRUNCATION_NOTICE.format(
        filename=INDEX_FILENAME,
        line_count=stats.line_count,
        byte_size=stats.byte_size,
        max_lines=INDEX_MAX_LINES,
        max_bytes=INDEX_MAX_BYTES,
    )
    return f"{kept}\n{notice}"
