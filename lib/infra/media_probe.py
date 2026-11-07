"""Media probing: read container formats, audio/video streams and durations with bundled ffmpeg, without ffprobe.

With ``-c copy -f framecrc``, ffmpeg demuxes without decoding and emits packet timestamps.
Stream duration covers packets not marked for discard; container formats come from the
``Input #0, <格式>, from`` line in ffmpeg input information.
Unit previews, asset bundles, Jianying draft export and audio upload validation use
:func:`probe_media`; frame timestamps (for example, contact-sheet frame extraction)
come from :func:`probe_video_frame_times`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from lib.infra.ffmpeg import ffmpeg_executable, local_file_input
from lib.infra.subprocess_deadline import (
    DEFAULT_TERMINATE_GRACE_SECONDS,
    Spawner,
    SubprocessDeadlineExceeded,
    run_with_deadline,
)

DEFAULT_PROBE_DEADLINE_SECONDS = 30.0

_AV_PKT_FLAG_DISCARD = 0x0004
_AV_NOPTS_VALUE = -(2**63)

_INPUT_FORMAT_RE = re.compile(r"^Input #0, (\S+), from ", re.MULTILINE)
_HEADER_RE = re.compile(r"^#(tb|media_type|codec_id) (\d+): (\S+)$")


class MediaProbeError(ValueError):
    """ffmpeg cannot parse the file, or probing timed out."""


@dataclass(frozen=True, slots=True)
class MediaStream:
    kind: str
    """``video`` / ``audio``."""
    codec: str | None
    duration_seconds: float | None
    """Duration covered by non-discarded packets in this stream; None when no packets can be timed."""
    packet_count: int
    """Number of non-discarded packets; usually the frame count for video streams."""


@dataclass(frozen=True, slots=True)
class MediaProbe:
    container_formats: frozenset[str]
    """Set of demuxer names, for example ``{"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}`` for m4a."""
    streams: tuple[MediaStream, ...]
    duration_seconds: float | None
    """Container duration: from the earliest start to the latest end across streams."""

    def first_stream(self, kind: str) -> MediaStream | None:
        return next((stream for stream in self.streams if stream.kind == kind), None)


@dataclass(slots=True)
class _StreamAccumulator:
    time_base: Fraction | None = None
    kind: str | None = None
    codec: str | None = None
    start: int | None = None
    end: int | None = None
    packet_count: int = 0

    def add_packet(self, pts: int, duration: int) -> None:
        self.packet_count += 1
        packet_end = pts + max(duration, 0)
        self.start = pts if self.start is None else min(self.start, pts)
        self.end = packet_end if self.end is None else max(self.end, packet_end)

    def span_seconds(self) -> tuple[Fraction, Fraction] | None:
        if self.time_base is None or self.start is None or self.end is None:
            return None
        # Encoder priming uses negative timestamps (for example, mp3 skip samples); visible content starts at 0.
        start = max(self.start, 0) * self.time_base
        end = self.end * self.time_base
        return (start, end) if end > start else None


def _parse_packet_flags(fields: list[str]) -> int:
    for item in fields[6:]:
        name, _, value = item.strip().partition("=")
        if name == "F":
            return int(value, 16)
    return 0


def _parse_framecrc(output: str) -> dict[int, _StreamAccumulator]:
    streams: dict[int, _StreamAccumulator] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            header = _HEADER_RE.match(line)
            if header is None:
                continue
            key, index, value = header.group(1), int(header.group(2)), header.group(3)
            stream = streams.setdefault(index, _StreamAccumulator())
            if key == "tb":
                numerator, _, denominator = value.partition("/")
                stream.time_base = Fraction(int(numerator), int(denominator))
            elif key == "media_type":
                stream.kind = value
            else:
                stream.codec = value
            continue
        fields = line.split(",")
        if len(fields) < 6:
            raise ValueError(f"framecrc 行字段不足：{line!r}")
        index, pts, duration = int(fields[0]), int(fields[2]), int(fields[3])
        if pts == _AV_NOPTS_VALUE or _parse_packet_flags(fields) & _AV_PKT_FLAG_DISCARD:
            continue
        streams.setdefault(index, _StreamAccumulator()).add_packet(pts, duration)
    return streams


def _build_probe(container_formats: frozenset[str], accumulators: dict[int, _StreamAccumulator]) -> MediaProbe:
    streams: list[MediaStream] = []
    spans: list[tuple[Fraction, Fraction]] = []
    for index in sorted(accumulators):
        accumulator = accumulators[index]
        if accumulator.kind is None:
            continue
        span = accumulator.span_seconds()
        if span is not None:
            spans.append(span)
        streams.append(
            MediaStream(
                kind=accumulator.kind,
                codec=accumulator.codec,
                duration_seconds=float(span[1] - span[0]) if span is not None else None,
                packet_count=accumulator.packet_count,
            )
        )
    container_duration = float(max(end for _, end in spans) - min(start for start, _ in spans)) if spans else None
    return MediaProbe(
        container_formats=container_formats,
        streams=tuple(streams),
        duration_seconds=container_duration,
    )


async def probe_media(
    path: Path,
    *,
    deadline_seconds: float = DEFAULT_PROBE_DEADLINE_SECONDS,
    grace: float = DEFAULT_TERMINATE_GRACE_SECONDS,
    spawn: Spawner | None = None,
) -> MediaProbe:
    """Probe container formats and audio/video streams in ``path``. Open inputs only through the local file protocol (see ``local_file_input``).

    Raises:
        FfmpegUnavailableError: Bundled ffmpeg is unavailable.
        MediaProbeError: The file cannot be parsed, has no audio/video streams, or probing timed out.
        OSError: The ffmpeg subprocess could not be started.
    """
    args = [
        ffmpeg_executable(),
        "-hide_banner",
        "-nostdin",
        "-nostats",
        *local_file_input(path),
        "-map",
        "0:v?",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-f",
        "framecrc",
        "-",
    ]
    try:
        result = await run_with_deadline(
            args,
            deadline_seconds=deadline_seconds,
            grace=grace,
            capture_stdout=True,
            capture_stderr=True,
            spawn=spawn,
        )
    except SubprocessDeadlineExceeded:
        raise MediaProbeError(f"媒体探测超时：{path.name}") from None
    if result.returncode != 0:
        raise MediaProbeError(f"媒体文件无法解析：{path.name}")
    input_format = _INPUT_FORMAT_RE.search(result.stderr.decode(errors="replace"))
    container_formats = frozenset(input_format.group(1).split(",")) if input_format else frozenset()
    try:
        accumulators = _parse_framecrc(result.stdout.decode(errors="replace"))
    except (ValueError, ZeroDivisionError) as exc:
        raise MediaProbeError(f"无法解析 ffmpeg 输出：{path.name}") from exc
    return _build_probe(container_formats, accumulators)


def _parse_frame_times(output: str) -> tuple[float, ...]:
    time_base: Fraction | None = None
    pts_values: list[int] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            header = _HEADER_RE.match(line)
            if header is not None and header.group(1) == "tb":
                numerator, _, denominator = header.group(3).partition("/")
                time_base = Fraction(int(numerator), int(denominator))
            continue
        fields = line.split(",")
        if len(fields) < 6:
            raise ValueError(f"framecrc 行字段不足：{line!r}")
        pts = int(fields[2])
        if pts == _AV_NOPTS_VALUE or _parse_packet_flags(fields) & _AV_PKT_FLAG_DISCARD:
            continue
        pts_values.append(pts)
    if time_base is None or not pts_values:
        return ()
    pts_values.sort()
    first = pts_values[0]
    return tuple(float((pts - first) * time_base) for pts in pts_values)


async def probe_video_frame_times(
    path: Path,
    *,
    deadline_seconds: float = DEFAULT_PROBE_DEADLINE_SECONDS,
    grace: float = DEFAULT_TERMINATE_GRACE_SECONDS,
    spawn: Spawner | None = None,
) -> tuple[float, ...]:
    """Start time in seconds of each frame in the first video stream, in playback order, with the first frame at 0.

    Demux without decoding: timestamps come from video packet pts; timestamp n corresponds to decoded frame n in playback order.

    Raises:
        FfmpegUnavailableError: Bundled ffmpeg is unavailable.
        MediaProbeError: The file cannot be parsed, has no video frames, or probing timed out.
        OSError: The ffmpeg subprocess could not be started.
    """
    args = [
        ffmpeg_executable(),
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-v",
        "error",
        *local_file_input(path),
        "-map",
        "0:v:0",
        "-c",
        "copy",
        "-f",
        "framecrc",
        "-",
    ]
    try:
        result = await run_with_deadline(
            args, deadline_seconds=deadline_seconds, grace=grace, capture_stdout=True, spawn=spawn
        )
    except SubprocessDeadlineExceeded:
        raise MediaProbeError(f"媒体探测超时：{path.name}") from None
    if result.returncode != 0:
        raise MediaProbeError(f"媒体文件无法解析：{path.name}")
    try:
        times = _parse_frame_times(result.stdout.decode(errors="replace"))
    except (ValueError, ZeroDivisionError) as exc:
        raise MediaProbeError(f"无法解析 ffmpeg 输出：{path.name}") from exc
    if not times:
        raise MediaProbeError(f"没有视频帧：{path.name}")
    return times
