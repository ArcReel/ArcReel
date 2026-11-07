"""Place one timeline revision into clips: pure functions with integer-microsecond times, shared by Jianying drafts/final cuts.

Arrange clips consecutively on the main track. Trims apply only to their underlying video version;
hold extensions append out-point frozen frames. Transitions attach to preceding clips and survive
only cuts before the next exported clip; the final exported clip’s transition has no effect.
Narration starts at host-clip starts. Subtitles follow narration on narration variants, otherwise
retain source times and display only portions within in/out points.
Place BGM through :func:`lib.edit_timeline.bgm.place_bgm`, with registration-cached gain × clip volume.
Preserve narration/subtitle overlaps, truncating only beyond timeline end. Jianying drafts distribute
overlaps across tracks via :func:`stack_tracks`; final-cut overlapping narration sounds simultaneously
and libass separates colliding subtitles.
Editing previews (frontend/src/components/canvas/edit/preview-tracks.ts ``placeSubtitles``) use
the same subtitle set but display only one at a time; update them together when placement rules change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from lib.bgm.library import BgmSource
from lib.edit_timeline.bgm import place_bgm
from lib.edit_timeline.model import EditClip, EditTimelineContent, Transition
from lib.edit_timeline.readout import effective_source_range_us
from lib.jianying_draft.basis import DraftNarration, DraftUnitBasis


@dataclass(frozen=True, slots=True)
class UnitCue:
    """Material-layer subtitle, timed relative to narration start when following narration, otherwise source-video start."""

    start_us: int
    duration_us: int
    text: str

    @property
    def end_us(self) -> int:
        return self.start_us + self.duration_us


@dataclass(frozen=True, slots=True)
class UnitMaterial:
    """Unit facts used as clip material layers, taken from current presentation models.

    ``source_gain`` switches provider audio (0 when generation had none); ``narration_*`` exists
    only when this unit uses the narration variant.
    """

    unit_id: str
    video_path: str
    video_version: int
    video_duration_us: int
    source_gain: float
    subtitles: tuple[UnitCue, ...]
    narration_path: str | None = None
    narration_duration_us: int | None = None

    @property
    def subtitles_follow_narration(self) -> bool:
        return self.narration_path is not None


@dataclass(frozen=True, slots=True)
class UnitMaterials:
    """Material layers for units referenced by a revision, plus their fingerprints entering generation bases."""

    materials: Mapping[str, UnitMaterial]
    bases: tuple[DraftUnitBasis, ...]


class UnitMaterialUnavailableError(Exception):
    """A unit currently has no projectable material layer, e.g. video lacks provable provenance."""

    def __init__(self, unit_id: str, message: str) -> None:
        super().__init__(message)
        self.unit_id = unit_id


class UnitMaterialSource(Protocol):
    """Get revision-referenced unit material layers by narration variant; raise :class:`UnitMaterialUnavailableError` if unavailable.

    Layers derive from current unit presentation models; the server implements/injects this source.
    """

    async def __call__(
        self, project_name: str, *, episode: int, content: EditTimelineContent, narration: DraftNarration
    ) -> UnitMaterials: ...


@dataclass(frozen=True, slots=True)
class PlacedClip:
    """Main-track clip; positive ``hold_us`` appends out-point frozen frames, and ``transition_to_next`` leads to the next clip."""

    clip_id: str
    unit_id: str
    video_path: str
    start_us: int
    source_in_us: int
    source_duration_us: int
    volume: float
    hold_us: int
    transition_to_next: Transition | None = None

    @property
    def source_out_us(self) -> int:
        return self.source_in_us + self.source_duration_us

    @property
    def hold_start_us(self) -> int:
        return self.start_us + self.source_duration_us


@dataclass(frozen=True, slots=True)
class PlacedNarration:
    clip_id: str
    audio_path: str
    start_us: int
    duration_us: int

    @property
    def end_us(self) -> int:
        return self.start_us + self.duration_us


@dataclass(frozen=True, slots=True)
class PlacedSubtitle:
    start_us: int
    duration_us: int
    text: str

    @property
    def end_us(self) -> int:
        return self.start_us + self.duration_us


@dataclass(frozen=True, slots=True)
class PlacedBgmAudio:
    """Audible BGM span; ``volume`` already combines loudness gain × clip volume, with microsecond fades."""

    clip_id: str
    audio_path: str
    start_us: int
    source_in_us: int
    duration_us: int
    volume: float
    fade_in_us: int
    fade_out_us: int


@dataclass(frozen=True, slots=True)
class DraftPlacement:
    duration_us: int
    clips: tuple[PlacedClip, ...]
    narrations: tuple[PlacedNarration, ...]
    subtitles: tuple[PlacedSubtitle, ...]
    bgm: tuple[PlacedBgmAudio, ...] = ()


def _source_window(clip: EditClip, unit: UnitMaterial) -> tuple[int, int]:
    """Trimmed source range ``[in, out)``; use the full source if current no longer matches the trim’s underlying version."""
    return effective_source_range_us(clip, unit.video_version, unit.video_duration_us)


def _clip_subtitles(clip: PlacedClip, unit: UnitMaterial, *, carries: bool) -> list[PlacedSubtitle]:
    if unit.subtitles_follow_narration:
        if not carries:
            return []
        return [PlacedSubtitle(clip.start_us + cue.start_us, cue.duration_us, cue.text) for cue in unit.subtitles]
    placed: list[PlacedSubtitle] = []
    for cue in unit.subtitles:
        start = max(cue.start_us, clip.source_in_us)
        end = min(cue.end_us, clip.source_out_us)
        if end > start:
            placed.append(PlacedSubtitle(clip.start_us + start - clip.source_in_us, end - start, cue.text))
    return placed


def _within_timeline[T: (PlacedNarration, PlacedSubtitle)](items: list[T], duration_us: int) -> tuple[T, ...]:
    """Order by start, truncate beyond timeline end, discard empty results, and retain overlaps."""
    kept: list[T] = []
    for item in sorted(items, key=lambda item: item.start_us):
        end = min(item.end_us, duration_us)
        if end > item.start_us:
            kept.append(replace(item, duration_us=end - item.start_us))
    return tuple(kept)


def stack_tracks[T: (PlacedNarration, PlacedSubtitle)](items: Sequence[T]) -> tuple[tuple[T, ...], ...]:
    """Distribute overlapping clips over as few nonoverlapping tracks as possible: in start order, use the first free track or create one if none is free."""
    tracks: list[list[T]] = []
    for item in sorted(items, key=lambda item: item.start_us):
        track = next((track for track in tracks if track[-1].end_us <= item.start_us), None)
        if track is None:
            tracks.append([item])
        else:
            track.append(item)
    return tuple(tuple(track) for track in tracks)


def place_timeline(
    content: EditTimelineContent,
    units: Mapping[str, UnitMaterial],
    bgm_sources: Mapping[str, BgmSource] | None = None,
) -> DraftPlacement:
    """Place clips from revision content; skip units missing from ``units`` because scripts deleted them.

    ``bgm_sources`` contains track-referenced BGM; omit clips absent from it (pre-render checks already rejected such timelines).
    """
    clips: list[PlacedClip] = []
    narrations: list[PlacedNarration] = []
    subtitles: list[PlacedSubtitle] = []
    cursor = 0
    for clip in content.clips:
        unit = units.get(clip.unit_id)
        if unit is None:
            continue
        source_in, source_out = _source_window(clip, unit)
        placed = PlacedClip(
            clip_id=clip.id,
            unit_id=clip.unit_id,
            video_path=unit.video_path,
            start_us=cursor,
            source_in_us=source_in,
            source_duration_us=source_out - source_in,
            volume=clip.source_volume * unit.source_gain,
            hold_us=clip.hold_us,
            transition_to_next=clip.transition_to_next,
        )
        clips.append(placed)
        if clip.carries_narration and unit.narration_path is not None and unit.narration_duration_us is not None:
            narrations.append(PlacedNarration(clip.id, unit.narration_path, cursor, unit.narration_duration_us))
        subtitles.extend(_clip_subtitles(placed, unit, carries=clip.carries_narration))
        cursor += placed.source_duration_us + placed.hold_us
    if clips and clips[-1].transition_to_next is not None:
        clips[-1] = replace(clips[-1], transition_to_next=None)
    sources = bgm_sources or {}
    return DraftPlacement(
        duration_us=cursor,
        clips=tuple(clips),
        narrations=_within_timeline(narrations, cursor),
        subtitles=_within_timeline(subtitles, cursor),
        bgm=tuple(
            PlacedBgmAudio(
                clip_id=placed.clip_id,
                audio_path=source.path,
                start_us=placed.start_us,
                source_in_us=placed.source_in_us,
                duration_us=placed.duration_us,
                volume=placed.volume * source.gain,
                fade_in_us=placed.fade_in_us,
                fade_out_us=placed.fade_out_us,
            )
            for placed in place_bgm(content.bgm, cursor)
            if (source := sources.get(placed.bgm_id)) is not None
        ),
    )


__all__ = [
    "DraftPlacement",
    "PlacedBgmAudio",
    "PlacedClip",
    "PlacedNarration",
    "PlacedSubtitle",
    "UnitCue",
    "UnitMaterial",
    "UnitMaterialSource",
    "UnitMaterialUnavailableError",
    "UnitMaterials",
    "place_timeline",
    "stack_tracks",
]
