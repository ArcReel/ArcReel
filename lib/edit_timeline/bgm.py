"""BGM-track placement: pure functions using integer microseconds. Read projection, final videos, and Jianying drafts share this placement.

Place BGM clips at absolute starts, ending after their trim ranges; only one track plays at a time
(guaranteed by batch editing). Truncate beyond timeline end with fixed
:data:`BGM_CUTOFF_FADE_MICROSECONDS` fade-out, ignoring clip fade-out duration. Clips wholly after
end are silent. If combined fades exceed placed duration: truncated clips preserve cutoff fade-out
before shortening fade-in; untruncated clips proportionally shorten both. Editing-view previews use
placed starts/ends/fades directly from readouts rather than recomputing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lib.bgm.library import BgmSource
from lib.edit_timeline.model import BgmClip

BGM_CUTOFF_FADE_MICROSECONDS = 1_000_000
"""Fade-out duration at cutoff for BGM clips truncated beyond timeline end."""


@dataclass(frozen=True, slots=True)
class PlacedBgm:
    """BGM actually sounding on the timeline: ``volume`` is clip volume before applying BGM loudness gain."""

    clip_id: str
    bgm_id: str
    start_us: int
    source_in_us: int
    duration_us: int
    volume: float
    fade_in_us: int
    fade_out_us: int
    truncated: bool

    @property
    def end_us(self) -> int:
        return self.start_us + self.duration_us


def bgm_clip_length_us(clip: BgmClip) -> int:
    return clip.out_us - clip.in_us


def place_bgm(bgm: Sequence[BgmClip], duration_us: int) -> tuple[PlacedBgm, ...]:
    """Arrange BGM clips by start, truncate at timeline end, and discard clips truncated to empty."""
    placed: list[PlacedBgm] = []
    for clip in sorted(bgm, key=lambda item: item.start_us):
        natural_end = clip.start_us + bgm_clip_length_us(clip)
        end = min(natural_end, duration_us)
        if end <= clip.start_us:
            continue
        length = end - clip.start_us
        truncated = end < natural_end
        fade_in, fade_out = _fit_fades(clip, length, truncated=truncated)
        placed.append(
            PlacedBgm(
                clip_id=clip.id,
                bgm_id=clip.bgm_id,
                start_us=clip.start_us,
                source_in_us=clip.in_us,
                duration_us=length,
                volume=clip.volume,
                fade_in_us=fade_in,
                fade_out_us=fade_out,
                truncated=truncated,
            )
        )
    return tuple(placed)


def _fit_fades(clip: BgmClip, length: int, *, truncated: bool) -> tuple[int, int]:
    if truncated:
        fade_out = min(BGM_CUTOFF_FADE_MICROSECONDS, length)
        return min(clip.fade_in_us, length - fade_out), fade_out
    total = clip.fade_in_us + clip.fade_out_us
    if total <= length:
        return clip.fade_in_us, clip.fade_out_us
    return clip.fade_in_us * length // total, clip.fade_out_us * length // total


def bgm_ids(bgm: Sequence[BgmClip]) -> tuple[str, ...]:
    """Referenced BGM IDs in first-appearance order."""
    return tuple(dict.fromkeys(clip.bgm_id for clip in bgm))


def bgm_sources_input(bgm: Sequence[BgmClip], sources: Mapping[str, BgmSource]) -> dict[str, object]:
    """BGM contribution to generation basis: content fingerprints/static gain per referenced BGM; record unavailable if inaccessible."""
    return {
        bgm_id: source.to_input() if (source := sources.get(bgm_id)) is not None else {"unavailable": True}
        for bgm_id in bgm_ids(bgm)
    }


__all__ = [
    "BGM_CUTOFF_FADE_MICROSECONDS",
    "PlacedBgm",
    "bgm_clip_length_us",
    "bgm_ids",
    "bgm_sources_input",
    "place_bgm",
]
