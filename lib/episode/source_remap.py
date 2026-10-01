"""整本源文改动时，切出集原文范围的重映射（ADR 0097）。

整本源文的一个文件被编辑或替换、删除、插入或调序时，只有原文范围触及这个文件的切出集受影响：

- **编辑、替换**（:func:`remap_for_edit`）：对改动前后的规范化文本做对齐，逐集映射原文范围的边界。范围内文字
  没变的集只平移；范围内文字有变化的集取映射后的范围，``text_changed``。恰好插在两集分界上的新文字归前一集；
  落在任何切出集之外的新文字（第一个切出集之前、最后一个之后、切出集之间的空段里）是未切分的原文。
- **删除**（:func:`remap_for_delete`）：等同于删掉这个文件的全部文字。跨进这个文件的集保留其余部分。
- **插入**（:func:`remap_for_insert`）：插入处落在一个跨文件的集内部时，新文件归这一集；否则不动任何集。
- **调序**（:func:`remap_for_move`）：原文范围不变。跨过被移动文件边界的集只保留起点所在文件里、调序后仍相连
  的部分。

映射后范围里没有非空白文字的集，``after`` 为 None：整段被删，由调用方按移除切分处理。

原文范围的偏移是各自文件内的下标，落在 ``normalize_source_text`` 的坐标系里；文件顺序以传入的 ``docs`` 为准。
服务之外的改动以快照为旧文本、以盘上文件为新文本，走同一套映射。本模块是纯函数，不读写磁盘与账本。
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lib.episode.episode_ledger import SourceDoc, SourceSpan

#: 逐字对齐的单侧字数上限。行级对齐后，替换块两侧都不超过它时再逐字细化；更大的块整体按替换处理。
_CHAR_ALIGN_LIMIT = 8000

Opcode = tuple[str, int, int, int, int]


@dataclass(frozen=True)
class TextAlignment:
    """改动前后两段文本的对齐：``difflib`` 风格的操作序列，依次覆盖旧文本与新文本的全部位置。"""

    opcodes: tuple[Opcode, ...]
    old_length: int
    new_length: int

    def map_position(self, position: int, *, after_insertions: bool) -> int:
        """旧文本位置在新文本里的对应位置。

        恰好在这个位置插入了新文字时，``after_insertions`` 决定落在插入文字之后还是之前。落在被替换的文字里时
        按比例换算，落在被删除的文字里时取删除处。
        """
        for tag, i1, _i2, j1, j2 in self.opcodes:
            if tag == "insert" and i1 == position:
                return j2 if after_insertions else j1
        for tag, i1, i2, j1, j2 in self.opcodes:
            if i1 <= position < i2:
                if tag == "equal":
                    return j1 + (position - i1)
                if tag == "delete":
                    return j1
                return j1 + (position - i1) * (j2 - j1) // (i2 - i1)
        return self.new_length if position >= self.old_length else position

    def changed_between(self, start: int, end: int, *, insertions_at_start: bool, insertions_at_end: bool) -> bool:
        """旧文本 ``[start, end)`` 里的文字是否有变化：其中有字被删改，或其中插入了新文字。

        恰好插在 ``start`` / ``end`` 上的新文字是否算在这段里，由两个参数决定。
        """
        for tag, i1, i2, _j1, _j2 in self.opcodes:
            if tag == "equal":
                continue
            if tag == "insert":
                if start < i1 < end or (i1 == start and insertions_at_start) or (i1 == end and insertions_at_end):
                    return True
            elif i1 < end and i2 > start:
                return True
        return False


def _line_offsets(lines: Sequence[str]) -> list[int]:
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return offsets


def _align_chars(old: str, new: str, old_base: int, new_base: int) -> list[Opcode]:
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    return [
        (tag, i1 + old_base, i2 + old_base, j1 + new_base, j2 + new_base)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
    ]


def _align_middle(old: str, new: str, old_base: int, new_base: int) -> list[Opcode]:
    """先按行对齐，替换块不大时再逐字细化。"""
    if not old or not new:
        if not old and not new:
            return []
        tag = "insert" if not old else "delete"
        return [(tag, old_base, old_base + len(old), new_base, new_base + len(new))]
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    old_offsets, new_offsets = _line_offsets(old_lines), _line_offsets(new_lines)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    opcodes: list[Opcode] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        a1, a2 = old_offsets[i1] + old_base, old_offsets[i2] + old_base
        b1, b2 = new_offsets[j1] + new_base, new_offsets[j2] + new_base
        if tag == "replace" and a2 - a1 <= _CHAR_ALIGN_LIMIT and b2 - b1 <= _CHAR_ALIGN_LIMIT:
            opcodes.extend(_align_chars(old[a1 - old_base : a2 - old_base], new[b1 - new_base : b2 - new_base], a1, b1))
        else:
            opcodes.append((tag, a1, a2, b1, b2))
    return opcodes


def align_texts(old: str, new: str) -> TextAlignment:
    """对齐改动前后的规范化文本。先去掉相同的开头与结尾，只对齐中间改动过的部分。"""
    limit = min(len(old), len(new))
    prefix = 0
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]:
        suffix += 1
    # 相同的开头与结尾退到整行：不然被删掉的整行与后面一行同样的开头字会被认成没删，改动落到错误的集里。
    # 一行太长时不退，避免整段没有换行的文本退成全文对齐
    line_start = old.rfind("\n", 0, prefix) + 1
    if prefix - line_start <= _CHAR_ALIGN_LIMIT:
        prefix = line_start
    tail = len(old) - suffix
    if tail > 0 and old[tail - 1] != "\n":
        newline = old.find("\n", tail)
        if newline != -1 and newline + 1 - tail <= _CHAR_ALIGN_LIMIT:
            suffix = len(old) - newline - 1
        elif newline == -1 and suffix <= _CHAR_ALIGN_LIMIT:
            suffix = 0
    opcodes: list[Opcode] = []
    if prefix:
        opcodes.append(("equal", 0, prefix, 0, prefix))
    opcodes.extend(_align_middle(old[prefix : len(old) - suffix], new[prefix : len(new) - suffix], prefix, prefix))
    if suffix:
        opcodes.append(("equal", len(old) - suffix, len(old), len(new) - suffix, len(new)))
    return TextAlignment(opcodes=tuple(opcodes), old_length=len(old), new_length=len(new))


@dataclass(frozen=True)
class RangeRemap:
    """一个切出集原文范围的重映射结果。"""

    episode: int
    before: SourceSpan
    #: 改动后的原文范围；范围里没有非空白文字（整段被删）时为 None。
    after: SourceSpan | None
    #: 范围内的文字有变化（含整段被删）。
    text_changed: bool


class _Files:
    """一组按顺序排列的文件：路径、长度与全局偏移。"""

    def __init__(self, docs: Sequence[SourceDoc]):
        self.docs = list(docs)
        self.rels = [doc.rel_path for doc in self.docs]
        self.bases = [0]
        for doc in self.docs:
            self.bases.append(self.bases[-1] + len(doc.text))

    def index(self, rel: str) -> int:
        return self.rels.index(rel)

    def global_start(self, span: SourceSpan) -> int:
        return self.bases[self.index(span.source_file)] + span.start

    def global_end(self, span: SourceSpan) -> int:
        return self.bases[self.index(span.end_file)] + span.end

    def text(self, span: SourceSpan) -> str:
        first, last = self.index(span.source_file), self.index(span.end_file)
        parts: list[str] = []
        for index in range(first, last + 1):
            text = self.docs[index].text
            parts.append(text[span.start if index == first else 0 : span.end if index == last else len(text)])
        return "".join(parts)

    def normalized(self, span: SourceSpan) -> SourceSpan:
        """把落在文件交界上的起止点挪到范围以内：起点在文件末尾时挪到下一个文件开头，终点在文件开头时挪到上一个文件末尾。"""
        first, start = self.index(span.source_file), span.start
        last, end = self.index(span.end_file), span.end
        while first < last and start >= len(self.docs[first].text):
            first, start = first + 1, 0
        while last > first and end <= 0:
            last -= 1
            end = len(self.docs[last].text)
        return SourceSpan(source_file=self.rels[first], start=start, end_file=self.rels[last], end=end)


def _finish(files: _Files, episode: int, before: SourceSpan, after: SourceSpan, changed: bool) -> RangeRemap:
    span = files.normalized(after)
    if (files.index(span.source_file), span.start) >= (files.index(span.end_file), span.end) or not files.text(
        span
    ).strip():
        return RangeRemap(episode=episode, before=before, after=None, text_changed=True)
    return RangeRemap(episode=episode, before=before, after=span, text_changed=changed)


def _unchanged(episode: int, span: SourceSpan) -> RangeRemap:
    return RangeRemap(episode=episode, before=span, after=span, text_changed=False)


def remap_for_edit(
    docs: Sequence[SourceDoc], spans: Mapping[int, SourceSpan], *, rel: str, new_text: str
) -> list[RangeRemap]:
    """文件 ``rel`` 的规范化全文改为 ``new_text`` 后各集的原文范围。``docs`` 是改动前的整本源文。"""
    old = _Files(docs)
    target = old.index(rel)
    old_text = old.docs[target].text
    alignment = align_texts(old_text, new_text)
    starts = {old.global_start(span) for span in spans.values()}
    new = _Files([*docs[:target], SourceDoc(rel_path=rel, text=new_text), *docs[target + 1 :]])
    results: list[RangeRemap] = []
    for episode, span in spans.items():
        first, last = old.index(span.source_file), old.index(span.end_file)
        if not first <= target <= last:
            results.append(_unchanged(episode, span))
            continue
        starts_here, ends_here = first == target, last == target
        low = span.start if starts_here else 0
        high = span.end if ends_here else len(old_text)
        # 终点与下一集起点相接时，插在分界上的新文字归这一集；终点之后是未切分的原文时不归
        shared_end = old.global_end(span) in starts
        include_end = not ends_here or shared_end
        changed = alignment.changed_between(
            low, high, insertions_at_start=not starts_here, insertions_at_end=include_end
        )
        new_low = alignment.map_position(low, after_insertions=starts_here)
        new_high = alignment.map_position(high, after_insertions=include_end)
        after = SourceSpan(
            source_file=span.source_file,
            start=new_low if starts_here else span.start,
            end_file=span.end_file,
            end=new_high if ends_here else span.end,
        )
        results.append(_finish(new, episode, span, after, changed))
    return results


def remap_for_delete(docs: Sequence[SourceDoc], spans: Mapping[int, SourceSpan], *, rel: str) -> list[RangeRemap]:
    """删除文件 ``rel`` 后各集的原文范围：等同于删掉这个文件的全部文字。"""
    old = _Files(docs)
    target = old.index(rel)
    new = _Files([doc for doc in docs if doc.rel_path != rel])
    results: list[RangeRemap] = []
    for episode, span in spans.items():
        first, last = old.index(span.source_file), old.index(span.end_file)
        if not first <= target <= last:
            results.append(_unchanged(episode, span))
            continue
        if first == last or (target + 1 > last and target - 1 < first):
            results.append(RangeRemap(episode=episode, before=span, after=None, text_changed=True))
            continue
        start_file, start = span.source_file, span.start
        if first == target:
            start_file, start = old.rels[target + 1], 0
        end_file, end = span.end_file, span.end
        if last == target:
            end_file, end = old.rels[target - 1], len(old.docs[target - 1].text)
        after = SourceSpan(source_file=start_file, start=start, end_file=end_file, end=end)
        results.append(_finish(new, episode, span, after, True))
    return results


def remap_for_insert(docs: Sequence[SourceDoc], spans: Mapping[int, SourceSpan], *, index: int) -> list[RangeRemap]:
    """在 ``docs`` 的第 ``index`` 个文件之前插入新文件后各集的原文范围。

    插入处落在一个跨文件的集内部时，新文件归这一集，``text_changed``；原文范围的记录不用改。
    """
    old = _Files(docs)
    boundary = old.bases[min(index, len(docs))]
    results: list[RangeRemap] = []
    for episode, span in spans.items():
        inside = old.global_start(span) < boundary < old.global_end(span)
        results.append(RangeRemap(episode=episode, before=span, after=span, text_changed=inside))
    return results


def remap_for_move(
    docs: Sequence[SourceDoc], spans: Mapping[int, SourceSpan], *, rel: str, new_index: int
) -> list[RangeRemap]:
    """把文件 ``rel`` 移到第 ``new_index`` 位后各集的原文范围。

    原文范围不变；跨过被移动文件边界的集只保留起点所在文件里、调序后仍相连的部分。
    """
    old = _Files(docs)
    order = [doc for doc in docs if doc.rel_path != rel]
    order.insert(new_index, old.docs[old.index(rel)])
    new = _Files(order)
    results: list[RangeRemap] = []
    for episode, span in spans.items():
        first, last = old.index(span.source_file), old.index(span.end_file)
        position = new.index(span.source_file)
        kept = first
        while kept < last and position + (kept - first) + 1 < len(new.rels):
            if new.rels[position + (kept - first) + 1] != old.rels[kept + 1]:
                break
            kept += 1
        if kept == last:
            results.append(_unchanged(episode, span))
            continue
        after = SourceSpan(
            source_file=span.source_file, start=span.start, end_file=old.rels[kept], end=len(old.docs[kept].text)
        )
        results.append(_finish(new, episode, span, after, True))
    return results


__all__ = [
    "RangeRemap",
    "TextAlignment",
    "align_texts",
    "remap_for_delete",
    "remap_for_edit",
    "remap_for_insert",
    "remap_for_move",
]
