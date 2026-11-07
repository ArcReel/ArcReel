"""Final-cut render planning maps revision clips onto the output-frame grid and splits at hard cuts.

Planning is pure. Round cumulative timeline durations to frames, never each segment independently,
keeping total frame count within half a frame of timeline duration without clip-count drift.
Pictures and episode mixes share the same frame boundaries.

Transitions never change clip boundaries. Their windows center on cuts, half before and half after:

- Overlapping transitions borrow half a window beyond the previous out-point/before the next in-point
  and crossfade within it. Insufficient source handles (or a previous clip with a hold extension)
  are filled with edge-frame freezes. Adjacent clips therefore share a segment.
- Nonoverlapping transitions (black/white flashes) borrow no media: fade the previous clip out over
  its final half-window and the next in over its opening half-window. Keep hard-cut segment boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path

from lib.edit_timeline.model import MICROSECONDS_PER_SECOND, EditClip, Transition
from lib.edit_timeline.readout import effective_source_range_us
from lib.edit_timeline.transitions import transition_preset

FINAL_CUT_FPS = 30
"""Fixed final-cut frame rate; normalize all source frame rates to it."""

_SHORT_EDGE = 1080


@dataclass(frozen=True, slots=True)
class OutputProfile:
    """Final-cut canvas and frame rate."""

    width: int
    height: int
    fps: int

    def frame_at(self, microseconds: int) -> int:
        """Nearest frame boundary to a timeline time."""
        return round(Fraction(microseconds * self.fps, MICROSECONDS_PER_SECOND))

    def seconds(self, frames: int) -> float:
        return frames / self.fps


def output_profile_for_aspect_ratio(aspect_ratio: str) -> OutputProfile:
    """Resolve project aspect ratio to final-cut canvas: short edge 1080, even long edge by ratio; unparseable ratios use 16:9."""
    width_part, _, height_part = aspect_ratio.partition(":")
    try:
        ratio = Fraction(int(width_part), int(height_part))
    except (ValueError, ZeroDivisionError):
        ratio = Fraction(16, 9)
    if ratio <= 0:
        ratio = Fraction(16, 9)
    if ratio >= 1:
        width, height = round(_SHORT_EDGE * ratio / 2) * 2, _SHORT_EDGE
    else:
        width, height = _SHORT_EDGE, round(_SHORT_EDGE / ratio / 2) * 2
    return OutputProfile(width=width, height=height, fps=FINAL_CUT_FPS)


@dataclass(frozen=True, slots=True)
class RenderMedia:
    """Render media for current video-unit video: path, version, measured duration, and presence of an audio stream."""

    path: Path
    video_version: int
    duration_us: int
    has_audio: bool


@dataclass(frozen=True, slots=True)
class RenderClip:
    """Media actually consumed by one clip, with source range resolved according to trim rules."""

    clip_id: str
    unit_id: str
    video_path: Path
    source_in_us: int
    source_duration_us: int
    hold_us: int
    source_volume: float
    has_audio: bool
    transition_to_next: Transition | None
    media_duration_us: int

    @property
    def source_out_us(self) -> int:
        return self.source_in_us + self.source_duration_us


def render_clips(clips: Sequence[EditClip], media: Mapping[str, RenderMedia]) -> tuple[RenderClip, ...]:
    """Resolve clip media in revision order; skip clips lacking render media because their units were deleted."""
    resolved: list[RenderClip] = []
    for clip in clips:
        unit_media = media.get(clip.unit_id)
        if unit_media is None:
            continue
        source_in, source_out = effective_source_range_us(clip, unit_media.video_version, unit_media.duration_us)
        resolved.append(
            RenderClip(
                clip_id=clip.id,
                unit_id=clip.unit_id,
                video_path=unit_media.path,
                source_in_us=source_in,
                source_duration_us=source_out - source_in,
                hold_us=clip.hold_us,
                source_volume=clip.source_volume,
                has_audio=unit_media.has_audio,
                transition_to_next=clip.transition_to_next,
                media_duration_us=unit_media.duration_us,
            )
        )
    return tuple(resolved)


@dataclass(frozen=True, slots=True)
class Extension:
    """Pictures borrowed outside clip intervals for overlap transitions: take ``source_frames`` source frames, then add ``freeze_frames`` frozen frames."""

    source_frames: int = 0
    freeze_frames: int = 0

    @property
    def frames(self) -> int:
        return self.source_frames + self.freeze_frames


def borrow_frames(needed: int, available_us: int, fps: int) -> Extension:
    """Borrow ``needed`` frames from ``available_us`` source handles; fill subframe shortfalls with edge-frame freezes."""
    available = max(available_us, 0) * fps // MICROSECONDS_PER_SECOND
    source = min(needed, available)
    return Extension(source_frames=source, freeze_frames=needed - source)


@dataclass(frozen=True, slots=True)
class Fade:
    """Fade-in/out at a clip end for nonoverlapping transitions."""

    frames: int
    color: str


@dataclass(frozen=True, slots=True)
class PlannedClip:
    """Clip on the frame grid: take ``source_frames`` source pictures, then add ``hold_frames`` using the out-point frame.

    ``lead`` / ``trail`` borrow pictures outside clip intervals for preceding/following overlapping
    transitions; ``fade_in`` / ``fade_out`` implement nonoverlapping transitions. None changes
    timeline frame boundaries.
    """

    clip: RenderClip
    start_frame: int
    source_frames: int
    hold_frames: int
    lead: Extension = Extension()
    trail: Extension = Extension()
    fade_in: Fade | None = None
    fade_out: Fade | None = None

    @property
    def frames(self) -> int:
        return self.source_frames + self.hold_frames

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frames

    @property
    def stream_frames(self) -> int:
        """Picture-frame count for rendering this clip: leading borrowed frames + own frames + trailing borrowed frames."""
        return self.lead.frames + self.frames + self.trail.frames


@dataclass(frozen=True, slots=True)
class Crossfade:
    """One overlapping transition within a segment; ``offset_frames`` locates window start relative to segment start."""

    effect: str
    offset_frames: int
    frames: int


@dataclass(frozen=True, slots=True)
class RenderSegment:
    """A segment between hard cuts: adjacent clips overlap-transition within it; segments concatenate directly."""

    clips: tuple[PlannedClip, ...]
    crossfades: tuple[Crossfade, ...] = ()

    @property
    def start_frame(self) -> int:
        return self.clips[0].start_frame

    @property
    def frames(self) -> int:
        return sum(planned.frames for planned in self.clips)


@dataclass(frozen=True, slots=True)
class RenderPlan:
    profile: OutputProfile
    segments: tuple[RenderSegment, ...]
    total_frames: int

    @property
    def clips(self) -> tuple[PlannedClip, ...]:
        return tuple(planned for segment in self.segments for planned in segment.clips)

    @property
    def duration_seconds(self) -> float:
        return self.profile.seconds(self.total_frames)


def _window_frames(transition: Transition, profile: OutputProfile) -> tuple[int, int]:
    """Transition-window frame counts before and after the cut."""
    frames = max(profile.frame_at(transition.duration_us), 1)
    before = frames // 2
    return before, frames - before


def _lay_out(clips: Sequence[RenderClip], profile: OutputProfile) -> tuple[list[PlannedClip], int]:
    planned: list[PlannedClip] = []
    cursor_us = 0
    for clip in clips:
        start = profile.frame_at(cursor_us)
        source_end = profile.frame_at(cursor_us + clip.source_duration_us)
        cursor_us += clip.source_duration_us + clip.hold_us
        end = profile.frame_at(cursor_us)
        if end > start:
            planned.append(
                PlannedClip(
                    clip=clip, start_frame=start, source_frames=source_end - start, hold_frames=end - source_end
                )
            )
    return planned, profile.frame_at(cursor_us)


def _join(previous: PlannedClip, following: PlannedClip, profile: OutputProfile) -> tuple[PlannedClip, PlannedClip]:
    """Rewrite clips on both sides of a cut according to the previous clip’s transition."""
    transition = previous.clip.transition_to_next
    if transition is None:
        return previous, following
    preset = transition_preset(transition.type)
    before, after = _window_frames(transition, profile)
    if preset.fade_color is not None:
        return (
            replace(previous, fade_out=Fade(min(before, previous.frames), preset.fade_color)),
            replace(following, fade_in=Fade(min(after, following.frames), preset.fade_color)),
        )
    # Hold-extended clips end on frozen pictures; continue that freeze through the transition window.
    tail_us = 0 if previous.hold_frames else previous.clip.media_duration_us - previous.clip.source_out_us
    return (
        replace(previous, trail=borrow_frames(after, tail_us, profile.fps)),
        replace(following, lead=borrow_frames(before, following.clip.source_in_us, profile.fps)),
    )


def _overlaps(planned: PlannedClip) -> bool:
    transition = planned.clip.transition_to_next
    return transition is not None and transition_preset(transition.type).overlaps


def plan_render(clips: Sequence[RenderClip], profile: OutputProfile) -> RenderPlan:
    """Arrange clips consecutively on the frame grid, expand transitions, then split at hard cuts; omit clips shorter than half a frame.

    Transitions apply only to cuts before the next included clip; the final clip’s transition has no effect.
    """
    planned, total_frames = _lay_out(clips, profile)
    for index in range(len(planned) - 1):
        planned[index], planned[index + 1] = _join(planned[index], planned[index + 1], profile)
    segments: list[RenderSegment] = []
    current: list[PlannedClip] = []
    crossfades: list[Crossfade] = []
    for index, item in enumerate(planned):
        current.append(item)
        transition = item.clip.transition_to_next
        if transition is not None and _overlaps(item) and index + 1 < len(planned):
            before, after = _window_frames(transition, profile)
            crossfades.append(
                Crossfade(
                    effect=transition_preset(transition.type).xfade,
                    offset_frames=item.end_frame - before - current[0].start_frame,
                    frames=before + after,
                )
            )
            continue
        segments.append(RenderSegment(clips=tuple(current), crossfades=tuple(crossfades)))
        current, crossfades = [], []
    return RenderPlan(profile=profile, segments=tuple(segments), total_frames=total_frames)


__all__ = [
    "FINAL_CUT_FPS",
    "Crossfade",
    "Extension",
    "Fade",
    "OutputProfile",
    "PlannedClip",
    "RenderClip",
    "RenderMedia",
    "RenderPlan",
    "RenderSegment",
    "borrow_frames",
    "output_profile_for_aspect_ratio",
    "plan_render",
    "render_clips",
]
