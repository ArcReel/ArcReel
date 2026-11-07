"""Source kinds: novels/scripts recorded per drama source file (ADR 0036).

- Whole-source manifests record ``whole_source_files[].source_kind``; standalone episodes use
  ``episodes[].source_kind``. Split episodes inherit starting-file kinds without separate storage; sourceless episodes lack kinds.
- Drama only; other content modes read/write no source kinds.
- Missing/invalid drama values read as novels.
- Project overviews use unanimous source kinds, otherwise novel for mixed/absent sources.

Read/write in-memory ``project.json`` only, without locks; callers hold project locks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, TypeGuard

from lib.episode.episode_ledger import parse_positive_episode_num, parse_source_range
from lib.episode.episode_sources import (
    WHOLE_SOURCE_FILES_KEY,
    SourceOrigin,
    episode_entry,
    episode_source_origin,
    whole_source_files,
)

SourceKind = Literal["novel", "screenplay"]

#: All source kinds.
SOURCE_KINDS: tuple[SourceKind, ...] = ("novel", "screenplay")

#: Default kinds.
DEFAULT_SOURCE_KIND: SourceKind = "novel"

#: Manifest/ledger field names.
SOURCE_KIND_FIELD = "source_kind"


def is_source_kind(value: object) -> TypeGuard[SourceKind]:
    return isinstance(value, str) and value in SOURCE_KINDS


def source_kind_applies(project: Mapping[str, Any]) -> bool:
    """Whether projects have source kinds, drama only."""
    return project.get("content_mode") == "drama"


def _recorded(raw: Mapping[str, Any] | None) -> SourceKind:
    value = raw.get(SOURCE_KIND_FIELD) if raw is not None else None
    return value if is_source_kind(value) else DEFAULT_SOURCE_KIND


def _whole_source_item(project: Mapping[str, Any], rel: str) -> Mapping[str, Any] | None:
    raw = project.get(WHOLE_SOURCE_FILES_KEY)
    return next(
        (
            item
            for item in (raw if isinstance(raw, list) else [])
            if isinstance(item, Mapping) and item.get("source_file") == rel
        ),
        None,
    )


def whole_source_file_kind(project: Mapping[str, Any], rel: str) -> SourceKind | None:
    """Whole-source ``rel`` kinds; None for nondrama/unlisted files."""
    if not source_kind_applies(project) or rel not in whole_source_files(project):
        return None
    return _recorded(_whole_source_item(project, rel))


def entry_source_kind(project: Mapping[str, Any], entry: Mapping[str, Any]) -> SourceKind | None:
    """Episode kinds: standalone entry values, split starting-file kinds, none for sourceless.

    Split ranges without manifest placement, absent/unlisted sources, read novel. Nondrama always None.
    """
    if not source_kind_applies(project):
        return None
    origin = episode_source_origin(entry)
    if origin is SourceOrigin.NONE:
        return None
    if origin is SourceOrigin.OWN:
        return _recorded(entry)
    span = parse_source_range(entry)
    if span is None or span.source_file not in whole_source_files(project):
        return DEFAULT_SOURCE_KIND
    return _recorded(_whole_source_item(project, span.source_file))


def episode_source_kind(project: Mapping[str, Any], episode: int) -> SourceKind | None:
    """Kind for episode ID ``episode`` via :func:`entry_source_kind`; None outside ledgers."""
    entry = episode_entry(project, episode)
    return None if entry is None else entry_source_kind(project, entry)


def _all_source_kinds(project: Mapping[str, Any]) -> Iterable[SourceKind]:
    for rel in whole_source_files(project):
        yield _recorded(_whole_source_item(project, rel))
    raw = project.get("episodes")
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, Mapping) and episode_source_origin(entry) is SourceOrigin.OWN:
            yield _recorded(entry)


def project_overview_source_kind(project: Mapping[str, Any]) -> SourceKind:
    """Overview kinds: unanimous whole-source/standalone sources use that kind, otherwise novel."""
    if not source_kind_applies(project):
        return DEFAULT_SOURCE_KIND
    kinds = set(_all_source_kinds(project))
    return kinds.pop() if len(kinds) == 1 else DEFAULT_SOURCE_KIND


def record_whole_source_file_kind(project: dict[str, Any], rel: str, kind: SourceKind | None) -> None:
    """Record manifest kinds; ``kind`` None defaults novel. No writes for nondrama."""
    if not source_kind_applies(project):
        return
    raw = project.get(WHOLE_SOURCE_FILES_KEY)
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and item.get("source_file") == rel:
            item[SOURCE_KIND_FIELD] = kind or DEFAULT_SOURCE_KIND


def record_episode_kind(project: Mapping[str, Any], entry: dict[str, Any], kind: SourceKind | None) -> None:
    """Record standalone episode kinds; ``kind`` None preserves existing values, otherwise novel when absent. Nondrama skips."""
    if not source_kind_applies(project):
        return
    if kind is not None:
        entry[SOURCE_KIND_FIELD] = kind
    elif not is_source_kind(entry.get(SOURCE_KIND_FIELD)):
        entry[SOURCE_KIND_FIELD] = DEFAULT_SOURCE_KIND


def episodes_from_whole_source_file(project: Mapping[str, Any], rel: str) -> list[int]:
    """Split episodes starting within whole-source ``rel``, broadcast order."""
    raw = project.get("episodes")
    ids: list[int] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping) or episode_source_origin(entry) is not SourceOrigin.WHOLE_SOURCE:
            continue
        span = parse_source_range(entry)
        episode = parse_positive_episode_num(entry.get("episode"))
        if span is not None and span.source_file == rel and episode is not None:
            ids.append(episode)
    return ids


__all__ = [
    "DEFAULT_SOURCE_KIND",
    "SOURCE_KINDS",
    "SOURCE_KIND_FIELD",
    "SourceKind",
    "entry_source_kind",
    "episode_source_kind",
    "episodes_from_whole_source_file",
    "is_source_kind",
    "project_overview_source_kind",
    "record_episode_kind",
    "record_whole_source_file_kind",
    "source_kind_applies",
    "whole_source_file_kind",
]
