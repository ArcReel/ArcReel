"""「分集」视图的只读投影：整本源文按集分段，外加每集的体量、首尾句与 ``source/`` 里没有登记的文件。

- 整本源文按清单顺序逐个文件给出规范化全文，切成「集」与「未切分的原文」两类段。段的偏移与
  ``source_range`` 同一坐标系（规范化文本的字符下标）。只含空白的未切分段不单独成段。
- 未切分段排在按源文位置最后一个切出集之前的，是夹在切出集之间的空段（``gap``）；其余是尚未分集的原文。
- 原文范围落不到清单文件里（没有范围、文件不在清单里、越界或与前一集重叠）的切出集不进左栏，
  体量按它的集文件计。
- 体量按项目的 ``source_language`` 数阅读单位，朗读时长按项目生效语速折算。

本模块只读，不取锁；读到的是调用瞬间的快照。
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
    cut_episode_placements,
    discover_sources,
    episode_source_origin,
    is_episode_source_file,
    is_whole_source_file_path,
    whole_source_files,
)
from lib.infra.text_metrics import count_reading_units, reading_unit_noun
from lib.speech.speech_rate import estimate_spoken_seconds, project_speech_rate_override

SegmentKind = Literal["episode", "unsplit"]
ReadingUnit = Literal["chars", "words"]


@dataclass(frozen=True)
class LayoutSegment:
    """整本源文一个文件里的一段：一集的原文，或未切分的原文。"""

    kind: SegmentKind
    start: int
    end: int
    text: str
    #: ``kind == "episode"`` 时是集 ID。
    episode: int | None = None
    #: 未切分段排在按源文位置最后一个切出集之前（夹在切出集之间，或在第一个切出集之前）。
    gap: bool = False
    units: int = 0


@dataclass(frozen=True)
class LayoutFile:
    """整本源文清单中的一个文件。"""

    source_file: str
    name: str
    #: 上传时的原始文件名；上传内容与规范化文本逐字节相同、没有留原件备份时为 None。
    original_filename: str | None
    #: 文件读不到（不存在、符号链接、非 UTF-8）时为 True，此时没有分段。
    missing: bool
    #: 规范化全文的字符数，是文件内偏移的上界；读不到时为 0。
    length: int
    units: int
    cut_units: int
    segments: list[LayoutSegment] = field(default_factory=list)


@dataclass(frozen=True)
class LayoutEpisode:
    """一集的原文体量与首尾句。"""

    episode: int
    origin: SourceOrigin
    #: 原文段出现在左栏整本源文里。
    placed: bool
    #: 原文段所在的整本源文文件（仅 ``placed``）。
    source_file: str | None
    #: 读不到原文（无原文的集，或集文件缺失）时为 None。
    units: int | None
    spoken_seconds: float | None
    first_sentence: str
    last_sentence: str


@dataclass(frozen=True)
class UnregisteredFile:
    """直接位于 ``source/`` 下、既不在整本源文清单里、也不是账本里任何一集的集文件的文本文件。"""

    name: str
    size: int
    #: 文件名能直接登记为整本源文（非下划线前缀，也不是 ``episode_N.txt``）。
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
    """``source/raw/`` 里的原件备份，按文件名主干索引；同主干多份时取字典序最后一份。"""
    raw_dir = project_dir / "source" / "raw"
    if raw_dir.is_symlink() or not raw_dir.is_dir():
        return {}
    return {path.stem: path.name for path in sorted(raw_dir.iterdir()) if path.is_file()}


def _file_segments(
    doc: SourceDoc,
    placements: list[CutPlacement],
    *,
    gap_until: int,
    language: str | None,
) -> list[LayoutSegment]:
    """一个文件切成的段。``gap_until`` 是最后一个切出集在本文件里的结尾：在它之前的未切分段是空段。"""
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

    cursor = 0
    for item in sorted(placements, key=lambda p: p.start):
        unsplit(cursor, item.start)
        text = doc.text[item.start : item.end]
        segments.append(
            LayoutSegment(
                kind="episode",
                start=item.start,
                end=item.end,
                text=text,
                episode=item.episode,
                units=count_reading_units(text, language),
            )
        )
        cursor = item.end
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
    """由 ``project.json`` 的内存形态与磁盘上的源文拼出「分集」视图的数据。"""
    language = _language(project)
    rate_override = project_speech_rate_override(project)
    docs = discover_sources(project_dir, project)
    readable = {doc.rel_path for doc in docs}
    placements = cut_episode_placements(project, docs)
    last = max(((p.file_index, p.end) for p in placements.values()), default=None)
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
                    source_file=rel, name=name, original_filename=original, missing=True, length=0, units=0, cut_units=0
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
            [p for p in placements.values() if p.file_index == index],
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
            )
        )

    episode_text: dict[int, tuple[str, str]] = {}
    for layout_file in files:
        for segment in layout_file.segments:
            if segment.episode is not None:
                episode_text[segment.episode] = (segment.text, layout_file.source_file)

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
                source_file=placed[1] if placed is not None else None,
                units=None if text is None else count_reading_units(text, language),
                spoken_seconds=None if text is None else estimate_spoken_seconds(text, language, rate_override),
                first_sentence=first,
                last_sentence=last_sentence,
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
