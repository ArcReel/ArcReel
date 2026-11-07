"""Shared MEMORY.md loading rules: line/byte counts, limit detection and truncation.

Both memory levels use these rules. Session assembly truncates the user-memory
index, while the listing API reports line_count / byte_size / over_limit. The
file cabinet's N/200 display therefore agrees with what the Agent reads;
over_limit means session assembly will truncate the index.

Limits follow native auto memory (CLI 2.1.259: MEMORY.md, 200 lines, 25,000).
Measure after strip(): outer blank lines do not enter context and should not
consume the allowance. Count actual UTF-8 bytes, not native JS .length (UTF-16
code units, where a Chinese character counts as 1). UTF-8 is more conservative
for Chinese indexes and matches the literal 25,000-byte limit.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Reserved index filename, serving as the memory table of contents.
INDEX_FILENAME = "MEMORY.md"

#: Loading limits: assembly truncates and listing reports ``over_limit``; writes are not blocked.
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25_000

#: Notice appended after truncation: the Agent cannot see discarded text and needs this cue
#: to reorganize the index rather than assuming the visible portion is the entire file.
_TRUNCATION_NOTICE = (
    "> Notice: `{filename}` has {line_count} lines / {byte_size} bytes, "
    "exceeding the loading limit of {max_lines} lines / {max_bytes} bytes; only the loaded portion is shown above. "
    "Keep each index entry on one line, within about 200 characters; put details in memory files."
)


@dataclass(frozen=True)
class MemoryIndexStats:
    """Index body size and limit status."""

    line_count: int
    byte_size: int

    @property
    def over_limit(self) -> bool:
        return self.line_count > INDEX_MAX_LINES or self.byte_size > INDEX_MAX_BYTES


def memory_index_stats(text: str) -> MemoryIndexStats:
    """Count index-body lines and UTF-8 bytes; whitespace-only content counts as zero."""
    trimmed = text.strip()
    if not trimmed:
        return MemoryIndexStats(line_count=0, byte_size=0)
    return MemoryIndexStats(line_count=trimmed.count("\n") + 1, byte_size=len(trimmed.encode("utf-8")))


def truncate_memory_index(text: str) -> str:
    """Truncate the index to loading limits, appending a notice when exceeded.

    First keep at most 200 lines, then cut at the final newline within 25,000 UTF-8
    bytes. Both checks are needed because a single line can be long. On a byte cut,
    errors="ignore" drops only the split code point, leaving no partial character.
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
