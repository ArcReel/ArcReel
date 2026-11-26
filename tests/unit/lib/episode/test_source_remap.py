"""整本源文改动时切出集原文范围的重映射：编辑、删除、插入、调序（ADR 0097）。"""

from __future__ import annotations

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from lib.episode.episode_ledger import SourceDoc, SourceSpan
from lib.episode.source_remap import (
    align_texts,
    remap_for_delete,
    remap_for_edit,
    remap_for_insert,
    remap_for_move,
)

A = "source/a.txt"
B = "source/b.txt"
C = "source/c.txt"

ONE = "第一章 春雨落下。\n"
TWO = "第二章 夏日炎炎。\n"
THREE = "第三章 秋风萧瑟。\n"


def _span(start: int, end: int, rel: str = A, end_file: str | None = None) -> SourceSpan:
    return SourceSpan(source_file=rel, start=start, end_file=end_file or rel, end=end)


def _by_episode(remaps):
    return {item.episode: item for item in remaps}


class TestAlignTexts:
    def test_positions_after_a_replaced_character_keep_their_offset(self):
        old = "甲乙丙丁戊"
        new = "甲乙X丁戊"
        alignment = align_texts(old, new)
        assert alignment.map_position(3, after_insertions=True) == 3
        assert alignment.changed_between(0, 2, insertions_at_start=False, insertions_at_end=False) is False
        assert alignment.changed_between(2, 4, insertions_at_start=False, insertions_at_end=False) is True

    def test_inserted_text_shifts_later_positions(self):
        alignment = align_texts("abcdef", "abcXYZdef")
        assert alignment.map_position(3, after_insertions=False) == 3
        assert alignment.map_position(3, after_insertions=True) == 6
        assert alignment.map_position(5, after_insertions=True) == 8

    def test_multi_line_edit_aligns_unchanged_lines(self):
        old = "".join(f"第{i}行内容保持不变。\n" for i in range(200))
        new = old.replace("第100行内容保持不变。", "第100行内容改了一个字。")
        alignment = align_texts(old, new)
        line_101 = old.index("第101行")
        assert alignment.map_position(line_101, after_insertions=True) == new.index("第101行")
        assert (
            alignment.changed_between(0, old.index("第100行"), insertions_at_start=False, insertions_at_end=False)
            is False
        )


@st.composite
def _source_edits(draw):
    lengths = draw(st.lists(st.integers(1, 5), min_size=2, max_size=4))
    gaps = draw(st.lists(st.integers(0, 2), min_size=len(lengths) + 1, max_size=len(lengths) + 1))
    size = sum(lengths) + sum(gaps)
    start = draw(st.integers(0, size))
    removed = draw(st.integers(0, size - start))
    inserted = draw(st.text(alphabet="新🌱", min_size=1 if removed == 0 else 0, max_size=4))
    return lengths, gaps, start, removed, inserted


@pytest.mark.invariant
@example(edit=([1, 1], [0, 0, 0], 1, 0, "新"))
@given(edit=_source_edits())
def test_known_edit_preserves_episode_character_ownership(edit):
    lengths, gaps, start, removed, inserted = edit
    owners: list[int | None] = [None] * gaps[0]
    spans = {}
    for episode, length in enumerate(lengths, 1):
        spans[episode] = _span(len(owners), len(owners) + length)
        owners.extend([episode] * length)
        owners.extend([None] * gaps[episode])
    old = "".join(chr(0x4E00 + i) for i in range(len(owners)))
    end = start + removed
    new = old[:start] + inserted + old[end:]
    result = _by_episode(remap_for_edit([SourceDoc(A, old)], spans, rel=A, new_text=new))
    assert result.keys() == spans.keys()

    removed_owners = set(owners[start:end])
    if removed == 0 or not inserted or len(removed_owners) == 1:
        if removed == 0:
            owner = owners[start - 1] if 0 < start < len(owners) and owners[start] is not None else None
        else:
            owner = owners[start]
        expected = owners[:start] + [owner] * len(inserted) + owners[end:]
        for episode, before in spans.items():
            positions = [i for i, item_owner in enumerate(expected) if item_owner == episode]
            after = _span(positions[0], positions[-1] + 1) if positions else None
            content = "".join(new[i] for i in positions)
            assert result[episode].after == after
            assert result[episode].text_changed == (content != old[before.start : before.end])
        return

    delta = len(inserted) - removed
    previous_end = 0
    for episode, before in spans.items():
        remap = result[episode]
        if before.end <= start:
            assert remap.after == before
            assert remap.text_changed is False
        elif before.start >= end:
            shifted = _span(before.start + delta, before.end + delta)
            assert remap.after == shifted
            assert remap.text_changed is False
            assert new[shifted.start : shifted.end] == old[before.start : before.end]
        else:
            assert remap.text_changed is True
            if before.start < start:
                assert remap.after is not None
                assert remap.after.start == before.start
                assert remap.after.end >= start
                assert new[before.start : start] == old[before.start : start]
            if before.end > end:
                assert remap.after is not None
                assert remap.after.end == before.end + delta
                assert remap.after.start <= start + len(inserted)
                assert new[end + delta : remap.after.end] == old[end : before.end]
        if remap.after is not None:
            assert previous_end <= remap.after.start < remap.after.end <= len(new)
            previous_end = remap.after.end


class TestRemapForEdit:
    def test_edit_inside_a_file_crossed_by_an_episode_changes_that_episode(self):
        docs = [SourceDoc(A, ONE), SourceDoc(B, TWO), SourceDoc(C, THREE)]
        spans = {1: _span(3, 4, A, C)}

        result = _by_episode(remap_for_edit(docs, spans, rel=B, new_text=TWO + "补一句。\n"))

        assert (result[1].after, result[1].text_changed) == (spans[1], True)

    def test_text_added_at_the_start_of_a_file_inside_a_crossing_episode_belongs_to_it(self):
        docs = [SourceDoc(A, ONE), SourceDoc(B, TWO)]
        spans = {1: _span(0, len(TWO), A, B)}

        result = _by_episode(remap_for_edit(docs, spans, rel=B, new_text="开头。" + TWO))

        assert result[1].after == _span(0, len("开头。" + TWO), A, B)
        assert result[1].text_changed is True

    def test_editing_a_file_no_episode_touches_changes_nothing(self):
        docs = [SourceDoc(A, ONE), SourceDoc(B, TWO)]
        spans = {1: _span(0, len(ONE))}

        result = _by_episode(remap_for_edit(docs, spans, rel=B, new_text="另起炉灶。\n"))

        assert (result[1].after, result[1].text_changed) == (spans[1], False)


class TestRemapForDelete:
    def test_an_episode_crossing_the_deleted_file_keeps_the_rest(self):
        docs = [SourceDoc(A, ONE), SourceDoc(B, TWO), SourceDoc(C, THREE)]
        spans = {1: _span(3, 5, A, C), 2: _span(5, len(THREE), C)}

        result = _by_episode(remap_for_delete(docs, spans, rel=B))

        assert (result[1].after, result[1].text_changed) == (spans[1], True)
        assert (result[2].after, result[2].text_changed) == (spans[2], False)

    def test_an_episode_only_in_the_deleted_file_has_no_range(self):
        docs = [SourceDoc(A, ONE), SourceDoc(B, TWO)]
        spans = {1: _span(0, len(ONE)), 2: _span(0, len(TWO), B)}

        result = _by_episode(remap_for_delete(docs, spans, rel=B))

        assert result[2].after is None
        assert (result[1].after, result[1].text_changed) == (spans[1], False)

    def test_an_episode_starting_in_the_deleted_file_starts_at_the_next_file(self):
        docs = [SourceDoc(A, ONE), SourceDoc(B, TWO), SourceDoc(C, THREE)]
        spans = {1: _span(2, 4, B, C)}

        result = _by_episode(remap_for_delete(docs, spans, rel=B))

        assert result[1].after == _span(0, 4, C)
        assert result[1].text_changed is True

    def test_an_episode_ending_in_the_deleted_file_ends_at_the_previous_file(self):
        docs = [SourceDoc(A, ONE), SourceDoc(B, TWO), SourceDoc(C, THREE)]
        spans = {1: _span(2, 4, A, B)}

        result = _by_episode(remap_for_delete(docs, spans, rel=B))

        assert result[1].after == _span(2, len(ONE), A)
        assert result[1].text_changed is True


class TestRemapForInsert:
    def test_a_file_inserted_inside_a_crossing_episode_belongs_to_it(self):
        docs = [SourceDoc(A, ONE), SourceDoc(C, THREE)]
        spans = {1: _span(3, 4, A, C)}

        result = _by_episode(remap_for_insert(docs, spans, index=1))

        assert (result[1].after, result[1].text_changed) == (spans[1], True)

    def test_a_file_inserted_on_a_boundary_between_episodes_changes_nothing(self):
        docs = [SourceDoc(A, ONE), SourceDoc(C, THREE)]
        spans = {1: _span(0, len(ONE)), 2: _span(0, len(THREE), C)}

        result = remap_for_insert(docs, spans, index=1)

        assert all(not item.text_changed and item.after == item.before for item in result)


class TestRemapForMove:
    def test_episodes_inside_the_moved_file_keep_their_range(self):
        docs = [SourceDoc(A, ONE), SourceDoc(B, TWO)]
        spans = {1: _span(0, len(ONE)), 2: _span(0, len(TWO), B)}

        result = remap_for_move(docs, spans, rel=B, new_index=0)

        assert all(not item.text_changed and item.after == item.before for item in result)

    def test_an_episode_split_apart_keeps_the_part_still_connected_to_its_start(self):
        docs = [SourceDoc(A, ONE), SourceDoc(B, TWO), SourceDoc(C, THREE)]
        spans = {1: _span(3, 4, A, B), 2: _span(4, len(THREE), C)}

        result = _by_episode(remap_for_move(docs, spans, rel=B, new_index=2))

        assert result[1].after == _span(3, len(ONE), A)
        assert result[1].text_changed is True
        assert (result[2].after, result[2].text_changed) == (spans[2], False)

    def test_an_episode_left_with_only_whitespace_has_no_range(self):
        docs = [SourceDoc(A, ONE + "\n"), SourceDoc(B, TWO)]
        spans = {1: _span(len(ONE), 3, A, B)}

        result = _by_episode(remap_for_move(docs, spans, rel=B, new_index=0))

        assert result[1].after is None


class TestRemapForEditWithoutLineBreaks:
    def test_a_typo_in_a_long_text_without_line_breaks_only_changes_its_episode(self):
        text = "春" * 20000 + "夏" * 20000
        docs = [SourceDoc(A, text)]
        spans = {1: _span(0, 20000), 2: _span(20000, 40000)}
        new_text = text[:30000] + "秋" + text[30001:]

        result = _by_episode(remap_for_edit(docs, spans, rel=A, new_text=new_text))

        assert (result[1].after, result[1].text_changed) == (spans[1], False)
        assert (result[2].after, result[2].text_changed) == (spans[2], True)
