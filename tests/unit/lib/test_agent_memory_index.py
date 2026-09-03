"""Tests for lib.agent_memory_index."""

import pytest

from lib.agent_memory_index import (
    INDEX_MAX_BYTES,
    INDEX_MAX_LINES,
    memory_index_stats,
    truncate_memory_index,
)


class TestStats:
    def test_counts_lines_and_utf8_bytes_of_the_stripped_body(self):
        stats = memory_index_stats("\n\n- 一条\n- 两条\n\n")

        assert (stats.line_count, stats.byte_size) == (2, len("- 一条\n- 两条".encode()))
        assert stats.over_limit is False

    def test_blank_text_counts_as_zero(self):
        assert memory_index_stats("   \n\n") == memory_index_stats("")
        assert memory_index_stats("").line_count == 0

    @pytest.mark.parametrize(
        ("text", "over_limit"),
        [
            ("line\n" * INDEX_MAX_LINES, False),
            ("line\n" * (INDEX_MAX_LINES + 1), True),
            ("x" * INDEX_MAX_BYTES, False),
            ("x" * (INDEX_MAX_BYTES + 1), True),
        ],
    )
    def test_over_limit_at_both_boundaries(self, text, over_limit):
        assert memory_index_stats(text).over_limit is over_limit


class TestTruncate:
    def test_passes_through_within_limits(self):
        assert truncate_memory_index("\n- 一条\n- 两条\n\n") == "- 一条\n- 两条"
        assert truncate_memory_index("   ") == ""

    def test_truncation_is_exactly_what_over_limit_predicts(self):
        """``over_limit`` 与截断同源：文件柜显示的超限与 Agent 实际读到的内容一致。"""
        for text in [
            "line\n" * INDEX_MAX_LINES,
            "line\n" * (INDEX_MAX_LINES + 1),
            "记" * (INDEX_MAX_BYTES // 3),
            "记" * INDEX_MAX_BYTES,
        ]:
            truncated = truncate_memory_index(text) != text.strip()
            assert truncated is memory_index_stats(text).over_limit

    def test_keeps_a_whole_prefix_within_both_limits(self):
        text = "\n".join(f"- 第 {i} 条：" + "记" * 1000 for i in range(300))

        kept = truncate_memory_index(text)

        body = kept.split("\n> 提示：")[0]
        assert body.count("\n") + 1 <= INDEX_MAX_LINES
        assert len(body.encode()) <= INDEX_MAX_BYTES
        assert text.startswith(body)

    def test_notice_reports_the_full_size(self):
        text = "\n".join(f"- 第 {i} 条" for i in range(300))

        notice = truncate_memory_index(text).rsplit("\n", 1)[-1]

        assert f"共 300 行 / {len(text.encode())} 字节" in notice
        assert f"{INDEX_MAX_LINES} 行 / {INDEX_MAX_BYTES} 字节" in notice
