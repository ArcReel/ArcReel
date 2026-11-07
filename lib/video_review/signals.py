"""Machine-check signals for video versions: black spans, frozen spans, and shot cuts.

Bundled ffmpeg computes all three locally in one decode (``blackdetect`` / ``freezedetect`` /
``scene`` scores), with first-frame time zero shared by contact-sheet labels and editing in/out
points. Signals are advisory: black/frozen spans are suspected issues; cuts are structural
information, not defects.

Cache results per video version in JSON sidecars: version snapshots never change after writing,
and video size/mtime fingerprint the cache. Increment :data:`SIGNALS_SCHEMA` when algorithm
parameters change to invalidate old caches.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.infra.async_thread import run_sync_transaction
from lib.infra.ffmpeg import ffmpeg_executable, local_file_input
from lib.infra.json_io import atomic_write_json, load_json_or_none
from lib.infra.subprocess_deadline import SubprocessDeadlineExceeded, run_with_deadline

logger = logging.getLogger(__name__)

SIGNALS_SCHEMA = 1

BLACK_MIN_SECONDS = 0.1
FREEZE_MIN_SECONDS = 0.5
FREEZE_NOISE_DB = -60
SCENE_THRESHOLD = 0.4

_DETECT_DEADLINE_SECONDS = 180.0
_FALLBACK_FRAME_SECONDS = 0.04
_TIME_EPSILON = 1e-4

TAG_BLACK = "BLACK"
TAG_FREEZE = "FREEZE"
TAG_CUT = "CUT"

_BLACK_RE = re.compile(r"black_start:(-?[\d.]+)\s+black_end:(-?[\d.]+)")
_FREEZE_START_RE = re.compile(r"lavfi\.freezedetect\.freeze_start:\s*(-?[\d.]+)")
_FREEZE_END_RE = re.compile(r"lavfi\.freezedetect\.freeze_end:\s*(-?[\d.]+)")
_SHOT_START_RE = re.compile(r"\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:(-?[\d.]+)")


class SignalDetectionError(RuntimeError):
    """Signal detection failed or timed out."""


@dataclass(frozen=True, slots=True)
class TimeSpan:
    start: float
    end: float
    """Half-open interval [start, end), in seconds."""

    def contains(self, time_seconds: float) -> bool:
        return self.start - _TIME_EPSILON <= time_seconds < self.end - _TIME_EPSILON


@dataclass(frozen=True, slots=True)
class VideoSignals:
    duration_seconds: float
    """Total video duration: final-frame start plus one frame duration."""
    black: tuple[TimeSpan, ...]
    freeze: tuple[TimeSpan, ...]
    cuts: tuple[float, ...]
    """Shot cuts: starting times of the new shots’ first frames."""

    @property
    def shots(self) -> tuple[TimeSpan, ...]:
        starts = (0.0, *self.cuts)
        ends = (*self.cuts, self.duration_seconds)
        return tuple(TimeSpan(start, end) for start, end in zip(starts, ends, strict=True))

    def tags_at(self, time_seconds: float) -> tuple[str, ...]:
        """Signals covering the frame starting at ``time_seconds``, always ordered CUT, BLACK, FREEZE."""
        tags: list[str] = []
        if any(abs(time_seconds - cut) <= _TIME_EPSILON for cut in self.cuts):
            tags.append(TAG_CUT)
        if any(span.contains(time_seconds) for span in self.black):
            tags.append(TAG_BLACK)
        if any(span.contains(time_seconds) for span in self.freeze):
            tags.append(TAG_FREEZE)
        return tuple(tags)

    def to_json(self) -> dict[str, Any]:
        return {
            "duration_seconds": self.duration_seconds,
            "black": [[span.start, span.end] for span in self.black],
            "freeze": [[span.start, span.end] for span in self.freeze],
            "cuts": list(self.cuts),
        }

    @classmethod
    def from_json(cls, data: Any) -> VideoSignals | None:
        """Return None for unexpected structure; callers treat it as a cache miss."""
        try:
            return cls(
                duration_seconds=float(data["duration_seconds"]),
                black=tuple(TimeSpan(float(start), float(end)) for start, end in data["black"]),
                freeze=tuple(TimeSpan(float(start), float(end)) for start, end in data["freeze"]),
                cuts=tuple(float(cut) for cut in data["cuts"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _video_duration(frame_times: Sequence[float]) -> float:
    last_frame_seconds = frame_times[-1] - frame_times[-2] if len(frame_times) > 1 else _FALLBACK_FRAME_SECONDS
    return frame_times[-1] + last_frame_seconds


def _without(spans: Sequence[TimeSpan], covered: Sequence[TimeSpan]) -> list[TimeSpan]:
    """Subtract ``covered`` from ``spans`` and discard remaining fragments shorter than :data:`FREEZE_MIN_SECONDS`."""
    remaining = list(spans)
    for hole in covered:
        pieces: list[TimeSpan] = []
        for span in remaining:
            if hole.end <= span.start or hole.start >= span.end:
                pieces.append(span)
                continue
            pieces.append(TimeSpan(span.start, hole.start))
            pieces.append(TimeSpan(hole.end, span.end))
        remaining = [piece for piece in pieces if piece.end - piece.start >= FREEZE_MIN_SECONDS - _TIME_EPSILON]
    return remaining


def _rounded(value: float) -> float:
    return round(value, 3)


def _parse_signals(log: str, duration: float) -> VideoSignals:
    black: list[TimeSpan] = []
    freeze: list[TimeSpan] = []
    cuts: list[float] = []
    freeze_start: float | None = None
    for line in log.splitlines():
        if (match := _BLACK_RE.search(line)) is not None:
            black.append(TimeSpan(float(match.group(1)), float(match.group(2))))
        elif (match := _FREEZE_START_RE.search(line)) is not None:
            freeze_start = float(match.group(1))
        elif (match := _FREEZE_END_RE.search(line)) is not None:
            if freeze_start is not None:
                freeze.append(TimeSpan(freeze_start, float(match.group(1))))
            freeze_start = None
        elif "showinfo" in line and (match := _SHOT_START_RE.search(line)) is not None:
            cuts.append(float(match.group(1)))
    if freeze_start is not None:
        # A freeze still active at video end gets no further ffmpeg freeze_end; extend it to video end.
        freeze.append(TimeSpan(freeze_start, duration))
    # Black spans are necessarily static; do not count them again as frozen spans.
    freeze = _without(freeze, black)
    return VideoSignals(
        duration_seconds=_rounded(duration),
        black=tuple(TimeSpan(_rounded(span.start), _rounded(span.end)) for span in black),
        freeze=tuple(TimeSpan(_rounded(span.start), _rounded(span.end)) for span in freeze),
        cuts=tuple(sorted({_rounded(cut) for cut in cuts if cut > _TIME_EPSILON})),
    )


async def detect_signals(video_path: Path, frame_times: Sequence[float]) -> VideoSignals:
    """Decode the entire video once to compute black spans, frozen spans, and shot cuts.

    Args:
        frame_times: Start times for every frame (``probe_video_frame_times`` output), used to determine total video duration.

    Raises:
        FfmpegUnavailableError: Bundled ffmpeg is unavailable.
        SignalDetectionError: Decoding failed or timed out.
    """
    filters = ",".join(
        (
            "setpts=PTS-STARTPTS",
            f"blackdetect=d={BLACK_MIN_SECONDS}",
            f"freezedetect=n={FREEZE_NOISE_DB}dB:d={FREEZE_MIN_SECONDS}",
            f"select='gt(scene,{SCENE_THRESHOLD})'",
            "showinfo",
        )
    )
    args = [
        ffmpeg_executable(),
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-v",
        "info",
        *local_file_input(video_path),
        "-map",
        "0:v:0",
        "-vf",
        filters,
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        result = await run_with_deadline(args, deadline_seconds=_DETECT_DEADLINE_SECONDS, capture_stderr=True)
    except SubprocessDeadlineExceeded:
        raise SignalDetectionError(f"信号检测超时：{video_path.name}") from None
    if result.returncode != 0:
        raise SignalDetectionError(f"信号检测失败：{video_path.name}")
    return _parse_signals(result.stderr.decode(errors="replace"), _video_duration(frame_times))


def _fingerprint(video_path: Path) -> list[int]:
    stat = video_path.stat()
    return [stat.st_size, stat.st_mtime_ns]


async def signals_for(video_path: Path, frame_times: Sequence[float], *, cache_file: Path) -> tuple[VideoSignals, bool]:
    """Read video-version signals from ``cache_file`` on a hit; otherwise detect and cache. Return (signals, cache hit).

    Cache-write failures affect only later recomputation, never this result.
    """
    fingerprint, cached = await asyncio.to_thread(lambda: (_fingerprint(video_path), load_json_or_none(cache_file)))
    if isinstance(cached, dict) and cached.get("schema") == SIGNALS_SCHEMA and cached.get("video") == fingerprint:
        signals = VideoSignals.from_json(cached.get("signals"))
        if signals is not None:
            return signals, True
    signals = await detect_signals(video_path, frame_times)
    await run_sync_transaction(_write_signals_cache, cache_file, fingerprint, signals)
    return signals, False


def _write_signals_cache(cache_file: Path, fingerprint: list[int], signals: VideoSignals) -> None:
    """Write signal caches; failures only log, allowing recomputation next time."""
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(cache_file, {"schema": SIGNALS_SCHEMA, "video": fingerprint, "signals": signals.to_json()})
    except OSError:
        logger.warning("视频信号缓存写入失败：%s", cache_file, exc_info=True)


def frame_index_at(frame_times: Sequence[float], time_seconds: float) -> int:
    """Index of the first frame starting no earlier than ``time_seconds`` within tolerance; clamp beyond the end to the final frame."""
    index = bisect.bisect_left(frame_times, time_seconds - _TIME_EPSILON)
    return min(index, len(frame_times) - 1)


def last_frame_index_before(frame_times: Sequence[float], time_seconds: float) -> int:
    """Index of the last frame starting before ``time_seconds`` within tolerance, or -1 if none."""
    return bisect.bisect_left(frame_times, time_seconds - _TIME_EPSILON) - 1


__all__ = [
    "SCENE_THRESHOLD",
    "SIGNALS_SCHEMA",
    "TAG_BLACK",
    "TAG_CUT",
    "TAG_FREEZE",
    "SignalDetectionError",
    "TimeSpan",
    "VideoSignals",
    "detect_signals",
    "frame_index_at",
    "last_frame_index_before",
    "signals_for",
]
