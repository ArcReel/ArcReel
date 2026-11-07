"""Remap split source ranges after whole-source changes (ADR 0097).

Editing/replacement/deletion/insertion/reordering affects only episodes touching changed files:

- **Edit/replace** (:func:`remap_for_edit`): align normalized old/new texts, mapping episode boundaries. Unchanged content
  shifts only; changed content takes mapped ranges with ``text_changed``. Insertions exactly between episodes belong to preceding episodes;
  insertions outside all splits, before first/after last/in gaps, stay unsplit.
- **Delete** (:func:`remap_for_delete`): delete all file text; spanning episodes retain remaining portions.
- **Insert** (:func:`remap_for_insert`): new files belong to spanning episodes when inserted inside them, otherwise unchanged.
- **Move** (:func:`remap_for_move`): preserve recorded ranges. Spanning episodes retain only reordered-connected
  portions anchored to their starting files.

Mapped ranges without nonwhitespace have ``after`` None, entirely deleted for caller split removal.

Offsets are per-file ``normalize_source_text`` coordinates; order follows supplied ``docs``.
External changes use snapshots as old/disk as new, sharing mapping. Pure functions, no disk/ledger I/O.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lib.episode.episode_ledger import SourceDoc, SourceSpan

#: Per-side character-refinement maximum after line alignment; refine only when both replacement sides fit, otherwise whole-block replacements.
_CHAR_ALIGN_LIMIT = 8000

Opcode = tuple[str, int, int, int, int]


@dataclass(frozen=True)
class TextAlignment:
    """``difflib``-style operations covering every old/new text position in sequence."""

    opcodes: tuple[Opcode, ...]
    old_length: int
    new_length: int

    def map_position(self, position: int, *, after_insertions: bool) -> int:
        """Old positions mapped to new text.

        At exact insertions, ``after_insertions`` chooses before/after inserted text. Replacement interiors
        map proportionally; deleted interiors map to deletion positions.
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
        """Whether old ``[start, end)`` changed through deletion/replacement/insertion.

        Two parameters decide whether insertions exactly at ``start`` / ``end`` count.
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
    """Align lines, refining small replacement blocks character-by-character."""
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
    """Align normalized old/new text after stripping identical prefixes/suffixes, processing changed middles only."""
    limit = min(len(old), len(new))
    prefix = 0
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]:
        suffix += 1
    # Back identical prefixes/suffixes to whole lines, preventing deleted-line prefixes matching following lines and assigning edits to wrong episodes.
    # Do not backtrack extremely long lines, avoiding whole-text alignment for unbroken paragraphs.
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
    """One split-range remap result."""

    episode: int
    before: SourceSpan
    #: New ranges, None when entirely deleted/no nonwhitespace.
    after: SourceSpan | None
    #: Changed interior text, including entire deletion.
    text_changed: bool


class _Files:
    """Ordered file paths/lengths/global offsets."""

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
        """Move file-boundary endpoints inward: end-of-file starts to next-file beginnings, beginning-of-file ends to previous-file endings."""
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
    """Episode ranges after normalized ``rel`` becomes ``new_text``; ``docs`` is prior whole sources."""
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
        # Boundary insertions belong to preceding episodes only when next starts touch, never when following spans remain unsplit.
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
    """Ranges after deleting ``rel``, equivalent to deleting every file character."""
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
    """Ranges after insertion before ``docs`` file ``index``.

    Insertions within spanning episodes belong to them, ``text_changed``, without changing range records.
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
    """Ranges after moving ``rel`` to ``new_index``.

    Preserve recorded ranges; spanning episodes keep reordered-connected portions in starting files only.
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
