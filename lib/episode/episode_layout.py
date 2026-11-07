"""Readonly Episodes projections: whole-source segments, episode volumes/edge sentences, unregistered ``source/`` files.

- Normalize full texts in manifest file order into episode/unsplit segments, using
  ``source_range`` character coordinates. Whitespace-only unsplit spans form no separate segments.
- Unsplit segments before last source-position split episodes are intervening gaps (``gap``); remaining spans are not yet episodized.
- Split episodes with unplaceable ranges (absent ranges/unlisted files/out-of-bounds/overlap) stay outside left columns,
  measuring volumes from episode files instead.
- Count reading units through project ``source_language``, converting speech durations via effective project rates.

Readonly, without locks, observing call-time snapshots.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from lib.episode.episode_excerpts import edge_sentences
from lib.episode.episode_ledger import (
    SOURCE_TEXT_SUFFIXES,
    SourceDoc,
    normalize_source_text,
    parse_positive_episode_num,
)
from lib.episode.episode_paths import episode_source_path
from lib.episode.episode_sources import (
    CutPlacement,
    SourceOrigin,
    changed_outside_service,
    cut_episode_placements,
    discover_sources,
    episode_source_origin,
    is_episode_source_file,
    is_whole_source_file_path,
    placement_text,
    whole_source_files,
)
from lib.episode.source_kinds import SourceKind, entry_source_kind, whole_source_file_kind
from lib.infra.text_metrics import count_reading_units, reading_unit_noun
from lib.speech.speech_rate import estimate_spoken_seconds, project_speech_rate_override

SegmentKind = Literal["episode", "unsplit"]
ReadingUnit = Literal["chars", "words"]


@dataclass(frozen=True)
class LayoutSegment:
    """One whole-source file segment: episode or unsplit source."""

    kind: SegmentKind
    start: int
    end: int
    text: str
    #: Episode ids when ``kind == "episode"``.
    episode: int | None = None
    #: Unsplit before last source-position split episodes, between splits or before the first.
    gap: bool = False
    units: int = 0
    #: Episode segments continuing from previous files, with earlier-file starts.
    continued: bool = False
    #: Episode segments continuing into following files, with later-file ends.
    continues: bool = False


@dataclass(frozen=True)
class LayoutFile:
    """One whole-source manifest file."""

    source_file: str
    name: str
    #: Original upload names; None when uploads equal normalized bytes without raw backups.
    original_filename: str | None
    #: True for unreadable/missing/symlink/non-UTF-8 files, without segments.
    missing: bool
    #: Normalized character totals bound per-file offsets; 0 if unreadable.
    length: int
    units: int
    cut_units: int
    segments: list[LayoutSegment] = field(default_factory=list)
    #: Source types, drama projects only.
    source_kind: SourceKind | None = None
    #: Sources externally changed without ledger updates, recorded fingerprints/snapshots differing from current text.
    changed_outside: bool = False


@dataclass(frozen=True)
class LayoutEpisode:
    """Episode source volume/edge sentences."""

    episode: int
    origin: SourceOrigin
    #: Source segments appear in left-column whole sources.
    placed: bool
    #: Whole-source start files, ``placed`` only.
    source_file: str | None
    #: None for unreadable sources, sourceless episodes or missing episode files.
    units: int | None
    spoken_seconds: float | None
    first_sentence: str
    last_sentence: str
    #: Source types: standalone entry values, split starting-file types; None for sourceless/nondrama episodes.
    source_kind: SourceKind | None = None
    #: Whole-source end files, ``placed`` only; noncrossing equals ``source_file``.
    end_file: str | None = None


@dataclass(frozen=True)
class UnregisteredFile:
    """Direct ``source/`` text files absent from whole-source manifests and all ledger episode filenames."""

    name: str
    size: int
    #: Filenames directly registerable in whole sources, not underscore-prefixed/``episode_N.txt``.
    can_join_whole_source: bool


@dataclass(frozen=True)
class EpisodeLayout:
    unit: ReadingUnit
    units: int
    cut_units: int
    files: list[LayoutFile]
    episodes: list[LayoutEpisode]
    unregistered: list[UnregisteredFile]


def _language(project: Mapping[str, Any]) -> str | None:
    raw = project.get("source_language")
    return raw if isinstance(raw, str) else None


def _entries(project: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = project.get("episodes")
    return [entry for entry in raw if isinstance(entry, Mapping)] if isinstance(raw, list) else []


def _raw_backup_names(project_dir: Path) -> dict[str, str]:
    """``source/raw/`` originals by stems; lexicographically last for multiple matches."""
    raw_dir = project_dir / "source" / "raw"
    if raw_dir.is_symlink() or not raw_dir.is_dir():
        return {}
    return {path.stem: path.name for path in sorted(raw_dir.iterdir()) if path.is_file()}


def _file_segments(
    doc: SourceDoc,
    file_index: int,
    placements: list[CutPlacement],
    *,
    gap_until: int,
    language: str | None,
) -> list[LayoutSegment]:
    """File segments. ``gap_until`` ends the last split episode within files; preceding unsplit spans are gaps."""
    segments: list[LayoutSegment] = []

    def unsplit(start: int, end: int) -> None:
        text = doc.text[start:end]
        if text.strip():
            segments.append(
                LayoutSegment(
                    kind="unsplit",
                    start=start,
                    end=end,
                    text=text,
                    gap=end <= gap_until,
                    units=count_reading_units(text, language),
                )
            )

    portions = [
        (portion, item) for item in placements if (portion := item.portion(file_index, len(doc.text))) is not None
    ]
    cursor = 0
    for (start, end), item in sorted(portions, key=lambda pair: pair[0]):
        unsplit(cursor, start)
        text = doc.text[start:end]
        segments.append(
            LayoutSegment(
                kind="episode",
                start=start,
                end=end,
                text=text,
                episode=item.episode,
                units=count_reading_units(text, language),
                continued=item.file_index < file_index,
                continues=item.end_file_index > file_index,
            )
        )
        cursor = end
    unsplit(cursor, len(doc.text))
    return segments


def _episode_file_text(project_dir: Path, episode: int) -> str | None:
    path = episode_source_path(project_dir, episode)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return normalize_source_text(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def _unregistered_files(project_dir: Path, project: Mapping[str, Any]) -> list[UnregisteredFile]:
    source_dir = project_dir / "source"
    if source_dir.is_symlink() or not source_dir.is_dir():
        return []
    registered = set(whole_source_files(project))
    found: list[UnregisteredFile] = []
    for path in sorted(source_dir.iterdir(), key=lambda p: p.name):
        rel = f"source/{path.name}"
        if (
            path.name.startswith(".")
            or path.suffix.lower() not in SOURCE_TEXT_SUFFIXES
            or path.is_symlink()
            or not path.is_file()
            or rel in registered
            or is_episode_source_file(project, rel)
        ):
            continue
        found.append(
            UnregisteredFile(
                name=path.name,
                size=path.stat().st_size,
                can_join_whole_source=is_whole_source_file_path(rel),
            )
        )
    return found


def build_episode_layout(project_dir: Path, project: Mapping[str, Any]) -> EpisodeLayout:
    """Build Episodes data from in-memory ``project.json`` and on-disk sources."""
    language = _language(project)
    rate_override = project_speech_rate_override(project)
    docs = discover_sources(project_dir, project)
    readable = {doc.rel_path for doc in docs}
    placements = cut_episode_placements(project, docs)
    last = max((p.end_position for p in placements.values()), default=None)
    raw_names = _raw_backup_names(project_dir)

    files: list[LayoutFile] = []
    doc_index = {doc.rel_path: index for index, doc in enumerate(docs)}
    for rel in whole_source_files(project):
        name = Path(rel).name
        original = raw_names.get(Path(rel).stem)
        index = doc_index.get(rel)
        if rel not in readable or index is None:
            files.append(
                LayoutFile(
                    source_file=rel,
                    name=name,
                    original_filename=original,
                    missing=True,
                    length=0,
                    units=0,
                    cut_units=0,
                    source_kind=whole_source_file_kind(project, rel),
                )
            )
            continue
        doc = docs[index]
        if last is None or index > last[0]:
            gap_until = -1
        elif index < last[0]:
            gap_until = len(doc.text)
        else:
            gap_until = last[1]
        segments = _file_segments(
            doc,
            index,
            list(placements.values()),
            gap_until=gap_until,
            language=language,
        )
        files.append(
            LayoutFile(
                source_file=rel,
                name=name,
                original_filename=original,
                missing=False,
                length=len(doc.text),
                units=count_reading_units(doc.text, language),
                cut_units=sum(s.units for s in segments if s.kind == "episode"),
                segments=segments,
                source_kind=whole_source_file_kind(project, rel),
                changed_outside=changed_outside_service(project_dir, project, doc),
            )
        )

    episode_text = {episode: (placement_text(docs, placement), placement) for episode, placement in placements.items()}

    episodes: list[LayoutEpisode] = []
    for entry in _entries(project):
        episode = parse_positive_episode_num(entry.get("episode"))
        if episode is None:
            continue
        origin = episode_source_origin(entry)
        placed = episode_text.get(episode)
        text: str | None
        if placed is not None:
            text = placed[0]
        elif origin is SourceOrigin.NONE:
            text = None
        else:
            text = _episode_file_text(project_dir, episode)
        first, last_sentence = edge_sentences(text) if text else ("", "")
        episodes.append(
            LayoutEpisode(
                episode=episode,
                origin=origin,
                placed=placed is not None,
                source_file=docs[placed[1].file_index].rel_path if placed is not None else None,
                units=None if text is None else count_reading_units(text, language),
                spoken_seconds=None if text is None else estimate_spoken_seconds(text, language, rate_override),
                first_sentence=first,
                last_sentence=last_sentence,
                source_kind=entry_source_kind(project, entry),
                end_file=docs[placed[1].end_file_index].rel_path if placed is not None else None,
            )
        )

    return EpisodeLayout(
        unit="words" if reading_unit_noun(language) == "词" else "chars",
        units=sum(f.units for f in files),
        cut_units=sum(f.cut_units for f in files),
        files=files,
        episodes=episodes,
        unregistered=_unregistered_files(project_dir, project),
    )


__all__ = [
    "EpisodeLayout",
    "LayoutEpisode",
    "LayoutFile",
    "LayoutSegment",
    "UnregisteredFile",
    "build_episode_layout",
]
