"""Episode source origins and whole-source file manifests: explicitly registered by projects, never inferred from ``source/`` contents (ADRs 0031, 0097).

- Each ledger entry records episode source origin through :data:`SOURCE_ORIGIN_FIELD`: cut from whole sources, standalone source, or sourceless.
  Cut episode files ``source/episode_N.txt`` derive from ledgers; standalone episode files are their own sources;
  sourceless episodes have no episode files, even if same-name files happen to exist in ``source/``.
- Top-level ``project.json`` :data:`WHOLE_SOURCE_FILES_KEY` lists whole-source files in creator-defined order, appending new files
  last. Unregistered ``source/`` files are neither whole sources nor any episode's source.
- Derive continuation planning starts from ledgers: the end of the last cut episode by source position
  (manifest file order, then in-file offset). Never store separate cursors.
- Files with registered cut episodes retain normalized-text snapshots under ``source/snapshots/``, providing alignment baselines for external edits.

This module reads/writes in-memory ``project.json`` representations and snapshot files without locks; callers use it inside project locks.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, TypeGuard

from lib.episode.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    SOURCE_TEXT_SUFFIXES,
    SourceDoc,
    SourceSpan,
    compute_source_fingerprints,
    is_derived_episode_name,
    mismatched_source_fingerprints,
    normalize_source_text,
    parse_positive_episode_num,
    parse_source_range,
)
from lib.episode.episode_paths import episode_source_relpath

logger = logging.getLogger(__name__)

#: Ledger-entry field: episode source origin.
SOURCE_ORIGIN_FIELD = "source_origin"

#: Top-level ``project.json`` field: whole-source file manifests, with elements like ``{"source_file": "source/novel.txt"}``.
WHOLE_SOURCE_FILES_KEY = "whole_source_files"

#: Normalized-text snapshot directory (project-relative).
SOURCE_SNAPSHOTS_DIR = "source/snapshots"

#: Top-level ``project.json`` field: whether whole sources retain text not planned into episodes (:func:`source_remaining` result).
#: ``ProjectManager`` records current sources when writing ``project.json``. Project lists use this for completion without reading sources;
#: external edits outside ArcReel, before ledger updates, can briefly disagree with production state.
SOURCE_REMAINING_KEY = "source_remaining"


class SourceOrigin(StrEnum):
    """An episode's source origin."""

    #: Ranges cut from whole sources by episode planning.
    WHOLE_SOURCE = "whole_source"
    #: Standalone sources uploaded or entered per episode by creators; episode files are their sources.
    OWN = "own"
    #: No source.
    NONE = "none"


SOURCE_ORIGINS: tuple[str, ...] = tuple(origin.value for origin in SourceOrigin)


def episode_source_origin(entry: Mapping[str, Any]) -> SourceOrigin:
    """Episode source origin recorded by entries.

    If missing or invalid, entries with ``source_range`` (regardless of structural completeness) count as cut from whole sources; others count as sourceless.
    Never inspect episode-file presence on disk. Structurally broken ranges therefore still undergo cut-episode validation rather than escaping as sourceless.
    """
    raw = entry.get(SOURCE_ORIGIN_FIELD)
    if isinstance(raw, str) and raw in SOURCE_ORIGINS:
        return SourceOrigin(raw)
    return SourceOrigin.WHOLE_SOURCE if entry.get("source_range") is not None else SourceOrigin.NONE


def is_cut_episode(entry: Mapping[str, Any]) -> bool:
    """Whether entries represent episodes cut from whole sources (including legacy split-flow episodes without source-range records)."""
    return episode_source_origin(entry) is SourceOrigin.WHOLE_SOURCE


def legacy_cut_episode_ids(project: Mapping[str, Any]) -> list[int]:
    """IDs cut from whole sources without source-range records (legacy split-flow episodes), in broadcast order.

    These episodes remain consumable but cannot support continuation planning: episode planning refuses them; replanning must start from episode 1.
    Standalone-source and sourceless episodes never occupy whole sources and are excluded.
    """
    ids: list[int] = []
    for entry in _entries(project):
        episode_id = parse_positive_episode_num(entry.get("episode"))
        if episode_id is not None and is_cut_episode(entry) and parse_source_range(entry) is None:
            ids.append(episode_id)
    return ids


def first_cut_episode_id(project: Mapping[str, Any]) -> int | None:
    """First cut episode ID in broadcast order; resetting from it is a full reset."""
    return next(
        (parse_positive_episode_num(entry.get("episode")) for entry in _entries(project) if is_cut_episode(entry)),
        None,
    )


def episode_entry(project: Mapping[str, Any], episode_id: int) -> Mapping[str, Any] | None:
    """First ledger entry whose episode ID is ``episode_id``."""
    return next(
        (entry for entry in _entries(project) if parse_positive_episode_num(entry.get("episode")) == episode_id),
        None,
    )


def is_episode_source_file(project: Mapping[str, Any], rel: str) -> bool:
    """Whether ``rel`` is a ledger episode file ``source/episode_N.txt`` (derived or standalone source).

    Sourceless episodes have no episode files; same-name disk files do not count.
    """
    return any(
        (episode_id := parse_positive_episode_num(entry.get("episode"))) is not None
        and rel == episode_source_relpath(episode_id)
        and episode_source_origin(entry) is not SourceOrigin.NONE
        for entry in _entries(project)
    )


def _entries(project: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    raw = project.get("episodes")
    return [entry for entry in raw if isinstance(entry, Mapping)] if isinstance(raw, list) else []


# ---------------------------------------------------------------------------
# Whole-source file manifests.
# ---------------------------------------------------------------------------


def is_whole_source_file_path(value: object) -> TypeGuard[str]:
    """Project-relative POSIX paths directly under ``source/``, with valid extensions and no dot / underscore prefix.

    Reserve ``episode_N.txt`` for episode files: planning writes cut episode files by ID, overwriting whole sources with that name.
    """
    if not isinstance(value, str) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        len(path.parts) == 2
        and path.parts[0] == "source"
        and not path.name.startswith((".", "_"))
        and path.suffix.lower() in SOURCE_TEXT_SUFFIXES
        and not is_derived_episode_name(path.name)
    )


def whole_source_files(project: Mapping[str, Any]) -> list[str]:
    """Whole-source files (project-relative paths) in manifest order; skip malformed or duplicate elements."""
    raw = project.get(WHOLE_SOURCE_FILES_KEY)
    files: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        rel = item.get("source_file") if isinstance(item, Mapping) else None
        if is_whole_source_file_path(rel) and rel not in files:
            files.append(rel)
    return files


def append_whole_source_file(project: dict[str, Any], rel: str, *, index: int | None = None) -> bool:
    """Register files in whole-source manifests; leave existing registrations unchanged, returning whether added.

    ``index`` is the resulting position in :func:`whole_source_files`; absent or beyond-end indices append, negative indices become 0.
    """
    if not is_whole_source_file_path(rel):
        raise ValueError(f"整本源文的文件须直接位于 source/ 下且为 .txt / .md：{rel}")
    files = whole_source_files(project)
    if rel in files:
        return False
    raw = project.get(WHOLE_SOURCE_FILES_KEY)
    items = list(raw) if isinstance(raw, list) else []
    position = len(items)
    if index is not None and index < len(files):
        # Manifests may contain malformed/duplicate elements; locate insertion points in raw lists by valid files.
        anchor = files[max(index, 0)]
        position = next(
            i for i, item in enumerate(items) if isinstance(item, Mapping) and item.get("source_file") == anchor
        )
    items.insert(position, {"source_file": rel})
    project[WHOLE_SOURCE_FILES_KEY] = items
    return True


def remove_whole_source_file(project: dict[str, Any], rel: str) -> bool:
    """Remove files from whole-source manifests, returning whether removed."""
    raw = project.get(WHOLE_SOURCE_FILES_KEY)
    if not isinstance(raw, list):
        return False
    kept = [item for item in raw if not (isinstance(item, Mapping) and item.get("source_file") == rel)]
    if len(kept) == len(raw):
        return False
    project[WHOLE_SOURCE_FILES_KEY] = kept
    return True


def _read_text_or_none(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("源文件读取失败，按缺失处理：%s: %s", path, exc)
        return None


def discover_sources(project_dir: Path, project: Mapping[str, Any]) -> list[SourceDoc]:
    """Whole-source files with normalized full texts in manifest order.

    Skip unreadable files (missing, symlink, non-UTF-8); a symlink/junction ``source/`` directory counts as no sources.
    """
    source_dir = project_dir / "source"
    if source_dir.is_symlink() or source_dir.is_junction() or not source_dir.is_dir():
        return []
    docs: list[SourceDoc] = []
    for rel in whole_source_files(project):
        path = project_dir / rel
        if path.is_symlink() or not path.is_file():
            continue
        text = _read_text_or_none(path)
        if text is not None:
            docs.append(SourceDoc(rel_path=rel, text=normalize_source_text(text)))
    return docs


# ---------------------------------------------------------------------------
# Planning starts derived from ledgers.
# ---------------------------------------------------------------------------


def planning_start(project: Mapping[str, Any], docs: list[SourceDoc]) -> tuple[str, int] | None:
    """Continuation planning start: end of the last cut episode by source position, or first file beginning without cut episodes.

    Ignore cut episodes whose range end file is absent from ``docs``; match files by NFC-normalized paths. Return None when whole sources have no files.
    """
    order = {unicodedata.normalize("NFC", doc.rel_path): index for index, doc in enumerate(docs)}
    last: tuple[int, int] | None = None
    for entry in _entries(project):
        if not is_cut_episode(entry):
            continue
        span = parse_source_range(entry)
        index = None if span is None else order.get(unicodedata.normalize("NFC", span.end_file))
        if span is None or index is None:
            continue
        if last is None or (index, span.end) > last:
            last = (index, span.end)
    if last is not None:
        return docs[last[0]].rel_path, last[1]
    return (docs[0].rel_path, 0) if docs else None


@dataclass(frozen=True)
class CutPlacement:
    """A cut episode's whole-source position: start ``(file_index, start)``, exclusive end ``(end_file_index, end)``.

    Indices address files in ``docs``; offsets are within their respective files. Ranges may span files, entirely including intermediate files.
    """

    episode: int
    file_index: int
    start: int
    end_file_index: int
    end: int

    @property
    def position(self) -> tuple[int, int]:
        """Source-position sort key: start-file order, then in-file offset."""
        return self.file_index, self.start

    @property
    def end_position(self) -> tuple[int, int]:
        """Source-position key for ends."""
        return self.end_file_index, self.end

    @property
    def crosses_files(self) -> bool:
        return self.end_file_index != self.file_index

    def portion(self, file_index: int, length: int) -> tuple[int, int] | None:
        """This episode's ``[start, end)`` portion within ``file_index`` (length ``length``); None if it does not traverse the file."""
        if not self.file_index <= file_index <= self.end_file_index:
            return None
        return (
            self.start if file_index == self.file_index else 0,
            self.end if file_index == self.end_file_index else length,
        )


def placement_text(docs: Sequence[SourceDoc], placement: CutPlacement) -> str:
    """Cut episode source: text concatenated in file order from start to end."""
    parts: list[str] = []
    for index in range(placement.file_index, placement.end_file_index + 1):
        text = docs[index].text
        portion = placement.portion(index, len(text))
        if portion is not None:
            parts.append(text[portion[0] : portion[1]])
    return "".join(parts)


def span_text(texts: Mapping[str, str], order: Sequence[str], span: SourceSpan) -> str | None:
    """Text for a ledger source range: ``texts`` contains normalized full file texts; ``order`` is whole-source file order.

    Single-file ranges ignore ``order``. For cross-file ranges, return None if endpoints are absent from ``order`` or reversed, file text is missing, or offsets out of bounds.
    """
    if not span.crosses_files:
        text = texts.get(span.source_file)
        return text[span.start : span.end] if text is not None and 0 <= span.start <= span.end <= len(text) else None
    if span.source_file not in order or span.end_file not in order:
        return None
    first, last = order.index(span.source_file), order.index(span.end_file)
    if last < first:
        return None
    parts: list[str] = []
    for index in range(first, last + 1):
        text = texts.get(order[index])
        if text is None:
            return None
        lo = span.start if index == first else 0
        hi = span.end if index == last else len(text)
        if not 0 <= lo <= hi <= len(text):
            return None
        parts.append(text[lo:hi])
    return "".join(parts)


def cut_episode_placements(project: Mapping[str, Any], docs: list[SourceDoc]) -> dict[int, CutPlacement]:
    """Placeable cut episodes within whole sources, indexed by episode ID.

    Do not place ranges whose endpoint files are absent from ``docs``, whose end file precedes the start file, or whose start is out of bounds; match NFC-normalized paths.
    Sort by source position and omit episodes overlapping the previous one. Clamp ends beyond file length. Episodes views and manual splitting identify episodes using this same placement.
    """
    order = {unicodedata.normalize("NFC", doc.rel_path): index for index, doc in enumerate(docs)}
    candidates: list[CutPlacement] = []
    for entry in _entries(project):
        episode = parse_positive_episode_num(entry.get("episode"))
        span = parse_source_range(entry)
        if episode is None or span is None or not is_cut_episode(entry):
            continue
        first = order.get(unicodedata.normalize("NFC", span.source_file))
        last = order.get(unicodedata.normalize("NFC", span.end_file))
        if first is None or last is None or last < first:
            continue
        if span.start < 0 or span.start > len(docs[first].text) or span.end < 0:
            continue
        if first == last and span.end < span.start:
            continue
        candidates.append(
            CutPlacement(
                episode=episode,
                file_index=first,
                start=span.start,
                end_file_index=last,
                end=min(span.end, len(docs[last].text)),
            )
        )
    placed: dict[int, CutPlacement] = {}
    cursor = (0, 0)
    for item in sorted(candidates, key=lambda p: (p.position, p.end_position)):
        if item.position < cursor or item.episode in placed:
            continue
        placed[item.episode] = item
        cursor = item.end_position
    return placed


def unsplit_range_ending_at(
    placements: Mapping[int, CutPlacement], *, file_index: int, end: int
) -> tuple[int, int] | None:
    """Unsplit file range ``[start, end)`` ending at ``end``: starts at the nearest preceding cut episode's end in this file (file beginning if none).

    Return None when ``end`` falls inside a cut episode's source.
    """
    ends_before = 0
    for p in placements.values():
        if not p.file_index <= file_index <= p.end_file_index:
            continue
        low = p.start if p.file_index == file_index else 0
        high = p.end if p.end_file_index == file_index else None
        if low < end and (high is None or end < high):
            return None
        if high is not None and high <= end:
            ends_before = max(ends_before, high)
    return ends_before, end


def cut_insert_index(
    entries: Sequence[object], placements: Mapping[int, CutPlacement], position: tuple[int, int]
) -> int:
    """Ledger insertion index for new cut episodes at source ``position``.

    Place after the nearest preceding cut episode by source position, or before the nearest following cut episode if none precedes. If no cut episodes are placeable,
    place after the last cut episode, or at the end if the ledger has no cut episodes.
    """
    ordered = sorted(placements.values(), key=lambda p: p.position)
    before = [p for p in ordered if p.position < position]
    after = [p for p in ordered if p.position >= position]

    def _index(episode: int) -> int:
        return next(
            i
            for i, entry in enumerate(entries)
            if isinstance(entry, Mapping) and parse_positive_episode_num(entry.get("episode")) == episode
        )

    if before:
        return _index(before[-1].episode) + 1
    if after:
        return _index(after[0].episode)
    return next(
        (
            i + 1
            for i in range(len(entries) - 1, -1, -1)
            if isinstance(entry := entries[i], Mapping) and is_cut_episode(entry)
        ),
        len(entries),
    )


def unplanned_text_remains(project: Mapping[str, Any], docs: list[SourceDoc]) -> bool:
    """Whether nonwhitespace source text remains after planning starts."""
    start = planning_start(project, docs)
    if start is None:
        return False
    rel, offset = start
    index = next(i for i, doc in enumerate(docs) if doc.rel_path == rel)
    if docs[index].text[offset:].strip():
        return True
    return any(doc.text.strip() for doc in docs[index + 1 :])


def source_fingerprints_diverged(project: Mapping[str, Any], docs: list[SourceDoc]) -> bool:
    """Sources changed after episode planning: recorded fingerprints differ from current sources."""
    return bool(mismatched_source_fingerprints(project.get(SOURCE_FINGERPRINTS_KEY), docs))


def source_remaining(project: Mapping[str, Any], docs: list[SourceDoc]) -> bool:
    """Whole sources retain text not planned into episodes; continuing episode planning is next.

    Nonwhitespace whole sources exist and either changed since planning (planning becomes reset), or retain nonwhitespace text after planning starts.
    Ad projects have no episode planning and always return False. Production-state continuation planning and project-list completion both use this predicate.
    """
    if project.get("content_mode") == "ad" or not any(doc.text.strip() for doc in docs):
        return False
    return source_fingerprints_diverged(project, docs) or unplanned_text_remains(project, docs)


def source_planning_inputs(project: Mapping[str, Any]) -> str:
    """All ``project.json`` inputs to :func:`source_remaining`, serialized to comparable strings.

    Writers use this to detect changes that could alter the result; unchanged inputs avoid rereading sources.
    """
    episodes = [
        {key: entry.get(key) for key in ("episode", SOURCE_ORIGIN_FIELD, "source_range")} for entry in _entries(project)
    ]
    return json.dumps(
        [
            project.get("content_mode"),
            project.get(WHOLE_SOURCE_FILES_KEY),
            project.get(SOURCE_FINGERPRINTS_KEY),
            episodes,
        ],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


def record_source_remaining(project_dir: Path, project: dict[str, Any]) -> None:
    """Write :func:`source_remaining` evaluated from on-disk whole sources to :data:`SOURCE_REMAINING_KEY` in ``project``."""
    project[SOURCE_REMAINING_KEY] = source_remaining(project, discover_sources(project_dir, project))


def stored_source_remaining(project: Mapping[str, Any]) -> bool:
    """Recorded :data:`SOURCE_REMAINING_KEY` in ``project.json``; absent/nonboolean means no remaining sources."""
    return project.get(SOURCE_REMAINING_KEY) is True


def archive_episode_file_path(path: Path) -> Path:
    """Episode-file archive paths: underscore prefix + ``.bak`` suffix, adding numbers for collisions.

    Underscore prefixes and ``.bak`` suffixes exclude archive files from whole-source filenames and ``episode_N.txt`` matching.
    """
    base = f"_{path.name}"
    candidate = path.with_name(f"{base}.bak")
    index = 1
    # exists() returns False for dangling symlinks; archive paths themselves can be dangling links and must count as occupied.
    while candidate.exists() or candidate.is_symlink():
        candidate = path.with_name(f"{base}.{index}.bak")
        index += 1
    return candidate


# ---------------------------------------------------------------------------
# Normalized-text snapshots.
# ---------------------------------------------------------------------------


def source_snapshot_path(project_dir: Path, rel: str) -> Path:
    """Snapshot path for whole-source file ``rel``."""
    return project_dir / SOURCE_SNAPSHOTS_DIR / PurePosixPath(rel).name


def cut_episode_source_files(project: Mapping[str, Any]) -> list[str]:
    """Whole-source files with registered cut episodes, deduplicated in first-seen broadcast order; include intermediate files traversed by spanning episodes."""
    order = whole_source_files(project)
    files: list[str] = []
    for entry in _entries(project):
        span = parse_source_range(entry) if is_cut_episode(entry) else None
        if span is None:
            continue
        if span.source_file in order and span.end_file in order:
            covered = order[order.index(span.source_file) : order.index(span.end_file) + 1] or [span.source_file]
        else:
            covered = [span.source_file, span.end_file]
        for rel in covered:
            if is_whole_source_file_path(rel) and rel not in files:
                files.append(rel)
    return files


def sync_source_snapshots(
    project_dir: Path,
    project: Mapping[str, Any],
    texts: Mapping[str, str],
    *,
    refreshed: Collection[str] | None = None,
) -> None:
    """Align snapshots with ledgers: write normalized full texts from ``texts`` for files with registered cut episodes; delete other snapshots.

    Preserve existing snapshots for files absent from ``texts``. When ``refreshed`` is supplied, only its files may overwrite snapshots for files changed outside
    the service; preserve snapshots of other changed files for old-text alignment when updating episode ledgers. Source fingerprints in ``project`` must still be the records
    preceding this write.
    """
    wanted = cut_episode_source_files(project)
    snapshot_dir = project_dir / SOURCE_SNAPSHOTS_DIR
    for rel in wanted:
        text = texts.get(rel)
        if text is None:
            continue
        if (
            refreshed is not None
            and rel not in refreshed
            and changed_outside_service(project_dir, project, SourceDoc(rel_path=rel, text=text))
        ):
            continue
        path = source_snapshot_path(project_dir, rel)
        if path.is_symlink():
            raise ValueError(f"源文快照不能是符号链接：{path.name}")
        if path.is_file() and _read_text_or_none(path) == text:
            continue
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    if not snapshot_dir.is_dir() or snapshot_dir.is_symlink():
        return
    keep = {PurePosixPath(rel).name for rel in wanted}
    for path in snapshot_dir.iterdir():
        if path.name not in keep and (path.is_file() or path.is_symlink()):
            path.unlink(
                missing_ok=True
            )  # hard-delete: derived -- expired whole-source snapshots, regenerable from ledgers


def read_source_snapshot(project_dir: Path, rel: str) -> str | None:
    """Snapshot text for whole-source file ``rel``; None if missing, a symlink, or unreadable."""
    path = source_snapshot_path(project_dir, rel)
    if path.is_symlink() or not path.is_file():
        return None
    return _read_text_or_none(path)


def changed_outside_service(project_dir: Path, project: Mapping[str, Any], doc: SourceDoc) -> bool:
    """Readable whole-source files changed outside the service: recorded fingerprints or snapshots differ from current normalized text.

    Files without recorded fingerprints or snapshots cannot be compared and do not count as changed.
    """
    raw = project.get(SOURCE_FINGERPRINTS_KEY)
    recorded = raw.get(doc.rel_path) if isinstance(raw, Mapping) else None
    if isinstance(recorded, str) and recorded != compute_source_fingerprints([doc])[doc.rel_path]:
        return True
    snapshot = read_source_snapshot(project_dir, doc.rel_path)
    return snapshot is not None and snapshot != doc.text


__all__ = [
    "SOURCE_ORIGINS",
    "SOURCE_ORIGIN_FIELD",
    "SOURCE_REMAINING_KEY",
    "SOURCE_SNAPSHOTS_DIR",
    "WHOLE_SOURCE_FILES_KEY",
    "CutPlacement",
    "SourceOrigin",
    "append_whole_source_file",
    "changed_outside_service",
    "cut_episode_placements",
    "cut_episode_source_files",
    "cut_insert_index",
    "discover_sources",
    "episode_entry",
    "episode_source_origin",
    "first_cut_episode_id",
    "is_cut_episode",
    "is_episode_source_file",
    "is_whole_source_file_path",
    "legacy_cut_episode_ids",
    "placement_text",
    "planning_start",
    "read_source_snapshot",
    "record_source_remaining",
    "remove_whole_source_file",
    "source_fingerprints_diverged",
    "source_planning_inputs",
    "source_remaining",
    "source_snapshot_path",
    "span_text",
    "stored_source_remaining",
    "sync_source_snapshots",
    "unplanned_text_remains",
    "unsplit_range_ending_at",
    "whole_source_files",
]
