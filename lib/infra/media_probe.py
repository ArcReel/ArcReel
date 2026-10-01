"""媒体探测：用随包 ffmpeg 读出容器格式、音视频流与时长，不依赖 ffprobe。

ffmpeg 以 ``-c copy -f framecrc`` 只解复用、不解码地逐包输出时间戳，流时长取未标记丢弃的
包所覆盖的区间；容器格式取 ffmpeg 输入信息里 ``Input #0, <格式>, from`` 一行。
单元预览、素材包、剪映草稿导出与音频上传校验都经 :func:`probe_media` 取得探测结果；
需要逐帧时刻时（如联系表按帧抽图）经 :func:`probe_video_frame_times`。
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
    """ffmpeg 无法解析该文件，或探测超时。"""


@dataclass(frozen=True, slots=True)
class MediaStream:
    kind: str
    """``video`` / ``audio``。"""
    codec: str | None
    duration_seconds: float | None
    """该流未丢弃的包覆盖的时长；没有可计时的包时为 None。"""
    packet_count: int
    """未丢弃的包数；视频流通常等于帧数。"""


@dataclass(frozen=True, slots=True)
class MediaProbe:
    container_formats: frozenset[str]
    """demuxer 名称集合，如 m4a 为 ``{"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}``。"""
    streams: tuple[MediaStream, ...]
    duration_seconds: float | None
    """容器时长：各流中最早起点到最晚终点。"""

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
        # 编码器前导填充以负时间戳出现（如 mp3 的 skip samples），可见内容从 0 开始。
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
    """探测 ``path`` 的容器格式与音视频流。输入只经本地文件协议打开（见 ``local_file_input``）。

    Raises:
        FfmpegUnavailableError: 随包 ffmpeg 不可用。
        MediaProbeError: 文件无法解析、没有音视频流，或探测超时。
        OSError: 无法启动 ffmpeg 子进程。
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
    """首个视频流每一帧的起始时刻（秒），按播放顺序排列，以首帧为 0。

    只解复用、不解码：时刻取自视频包的 pts，第 n 个时刻对应解码后按播放顺序的第 n 帧。

    Raises:
        FfmpegUnavailableError: 随包 ffmpeg 不可用。
        MediaProbeError: 文件无法解析、没有视频帧，或探测超时。
        OSError: 无法启动 ffmpeg 子进程。
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
