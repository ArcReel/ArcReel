"""媒体探测：用随包 ffmpeg 读出容器格式、音视频流、时长与流参数，不依赖 ffprobe。

ffmpeg 以 ``-c copy -f framecrc`` 只解复用、不解码地逐包输出时间戳，流时长取未标记丢弃的
包所覆盖的区间；容器格式取 ffmpeg 输入信息里 ``Input #0, <格式>, from`` 一行；流参数（分辨率、
像素格式、标称帧率、采样率等）解析自同一输入信息的 ``Stream #N:M`` 行；帧率用包时间戳实测均值，
与文件标称帧率（tbr）明显偏离时判为可变帧率；显示旋转读自 displaymatrix 侧数据。
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

_STREAM_LINE_RE = re.compile(r"^ *Stream #\d+:\d+(?:\[[^\]]*\])?(?:\([^)]*\))?: (Video|Audio): (.*)$")
_GEOMETRY_RE = re.compile(r",\s*(\d+)x(\d+)")
_TBR_RE = re.compile(r",\s*(\d+(?:\.\d+)?) tbr,")
_PIXFMT_RE = re.compile(r",\s*([a-z0-9]+)(?:\(([^)]*)\))?")
_ROTATION_RE = re.compile(r"displaymatrix: rotation of (-?\d+(?:\.\d+)?) degrees detected")
_SAMPLE_RATE_RE = re.compile(r",\s*(\d+) Hz,")
_CHANNELS_RE = re.compile(r",\s*(mono|stereo|2\.1|3\.0|4\.0|5\.0|5\.1|7\.1|quad|hexagonal|octagonal),")
_PIXEL_DEPTH_RE = re.compile(r"p(\d{2,3})[lb]e$")

_CHANNEL_COUNTS = {
    "mono": 1,
    "stereo": 2,
    "2.1": 3,
    "3.0": 3,
    "4.0": 4,
    "5.0": 5,
    "5.1": 6,
    "7.1": 8,
    "quad": 4,
    "hexagonal": 6,
    "octagonal": 8,
}

_COLOR_TRANSFER_TAGS = frozenset(
    {
        "bt709",
        "gamma22",
        "gamma28",
        "smpte170m",
        "smpte240m",
        "linear",
        "log100",
        "log316",
        "iec61966-2-4",
        "bt1361e",
        "iec61966-2-1",
        "bt2020-10",
        "bt2020-12",
        "smpte2084",
        "smpte4281",
        "arib-std-b67",
    }
)


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
    width: int | None = None
    """画面宽度（像素）；非视频流为 None。"""
    height: int | None = None
    """画面高度（像素）；非视频流为 None。"""
    fps: float | None = None
    """实测平均帧率：包数除以覆盖时长；测不出（音频、包数不足）时为 None。"""
    variable_frame_rate: bool = False
    """实测平均帧率与文件标称帧率（tbr）明显偏离，即可变帧率。"""
    rotation: float | None = None
    """displaymatrix 侧数据的显示旋转角（度），如 -90.0；文件无该侧数据时为 None。"""
    bit_depth: int | None = None
    """像素格式推导的采样位深（8/10/12/16）；推不出时为 None。"""
    color_transfer: str | None = None
    """像素格式的传函（trc），如 bt709 / smpte2084 / arib-std-b67；文件未声明或为 unknown 时为 None。"""
    sample_rate: int | None = None
    """音频采样率（Hz）；非音频流为 None。"""
    channel_count: int | None = None
    """音频声道数；非音频流为 None。"""

    @property
    def is_hdr(self) -> bool:
        """传函为 HDR（PQ / HLG）。"""
        return self.color_transfer in ("smpte2084", "arib-std-b67")


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


@dataclass(slots=True)
class _BannerStream:
    """ffmpeg 输入信息里一条 ``Stream`` 行（及其侧数据行）解析出的流参数。"""

    kind: str
    width: int | None = None
    height: int | None = None
    tbr: float | None = None
    rotation: float | None = None
    bit_depth: int | None = None
    color_transfer: str | None = None
    sample_rate: int | None = None
    channel_count: int | None = None


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


def _bit_depth(pix_fmt: str) -> int | None:
    match = _PIXEL_DEPTH_RE.search(pix_fmt)
    if match:
        return int(match.group(1))
    return 8 if pix_fmt.endswith("p") else None


def _color_transfer(tag_group: str | None) -> str | None:
    """像素格式括号内标签里的传函（trc）。

    标签形如 ``range, colorspace/trc/primaries, props``（如 ``tv, bt709/unknown/unknown, progressive``），
    只有三元组的中位才是传函；未声明或 unknown 时为 None。
    """
    if not tag_group:
        return None
    for tag in tag_group.split(","):
        parts = tag.strip().split("/")
        if len(parts) >= 2 and parts[1] in _COLOR_TRANSFER_TAGS:
            return parts[1]
    return None


def _parse_banner(stderr: str) -> tuple[_BannerStream, ...]:
    """解析 ffmpeg 输入信息里的 Stream 行与 displaymatrix 侧数据。"""
    streams: list[_BannerStream] = []
    current: _BannerStream | None = None
    for line in stderr.splitlines():
        match = _STREAM_LINE_RE.match(line)
        if match is not None:
            current = _BannerStream(kind=match.group(1).lower())
            streams.append(current)
            rest = match.group(2)
            if current.kind == "video":
                geometry = _GEOMETRY_RE.search(rest)
                if geometry:
                    current.width = int(geometry.group(1))
                    current.height = int(geometry.group(2))
                tbr = _TBR_RE.search(rest)
                if tbr:
                    current.tbr = float(tbr.group(1))
                pix_fmt = _PIXFMT_RE.search(rest)
                if pix_fmt:
                    current.bit_depth = _bit_depth(pix_fmt.group(1))
                    current.color_transfer = _color_transfer(pix_fmt.group(2))
            else:
                sample_rate = _SAMPLE_RATE_RE.search(rest)
                if sample_rate:
                    current.sample_rate = int(sample_rate.group(1))
                channels = _CHANNELS_RE.search(rest)
                if channels:
                    current.channel_count = _CHANNEL_COUNTS[channels.group(1)]
            continue
        rotation = _ROTATION_RE.search(line)
        if rotation is not None and current is not None:
            current.rotation = float(rotation.group(1))
    return tuple(streams)


def _measured_fps(accumulator: _StreamAccumulator, span: tuple[Fraction, Fraction] | None) -> float | None:
    if accumulator.packet_count < 2 or span is None:
        return None
    seconds = float(span[1] - span[0])
    if seconds <= 0:
        return None
    return round(accumulator.packet_count / seconds, 3)


def _is_variable_fps(fps: float | None, tbr: float | None) -> bool:
    if fps is None or tbr is None or tbr <= 0:
        return False
    return abs(fps - tbr) > max(0.02 * tbr, 0.5)


def _build_stream(
    kind: str,
    accumulator: _StreamAccumulator,
    span: tuple[Fraction, Fraction] | None,
    banner_by_kind: dict[str, list[_BannerStream]],
) -> MediaStream:
    info: _BannerStream | None = None
    if kind in ("video", "audio"):
        queue = banner_by_kind.get(kind)
        if queue:
            info = queue.pop(0)
    duration = float(span[1] - span[0]) if span is not None else None
    if kind == "video":
        fps = _measured_fps(accumulator, span)
        return MediaStream(
            kind=kind,
            codec=accumulator.codec,
            duration_seconds=duration,
            packet_count=accumulator.packet_count,
            width=info.width if info else None,
            height=info.height if info else None,
            fps=fps,
            variable_frame_rate=_is_variable_fps(fps, info.tbr if info else None),
            rotation=info.rotation if info else None,
            bit_depth=info.bit_depth if info else None,
            color_transfer=info.color_transfer if info else None,
        )
    if kind == "audio":
        return MediaStream(
            kind=kind,
            codec=accumulator.codec,
            duration_seconds=duration,
            packet_count=accumulator.packet_count,
            sample_rate=info.sample_rate if info else None,
            channel_count=info.channel_count if info else None,
        )
    return MediaStream(
        kind=kind,
        codec=accumulator.codec,
        duration_seconds=duration,
        packet_count=accumulator.packet_count,
    )


def _build_probe(
    container_formats: frozenset[str],
    accumulators: dict[int, _StreamAccumulator],
    banner: tuple[_BannerStream, ...],
) -> MediaProbe:
    streams: list[MediaStream] = []
    spans: list[tuple[Fraction, Fraction]] = []
    banner_by_kind: dict[str, list[_BannerStream]] = {}
    for banner_stream in banner:
        banner_by_kind.setdefault(banner_stream.kind, []).append(banner_stream)
    for index in sorted(accumulators):
        accumulator = accumulators[index]
        kind = accumulator.kind
        if kind is None:
            continue
        span = accumulator.span_seconds()
        if span is not None:
            spans.append(span)
        streams.append(_build_stream(kind, accumulator, span, banner_by_kind))
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
    """探测 ``path`` 的容器格式、音视频流与时长。输入只经本地文件协议打开（见 ``local_file_input``）。

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
    stderr = result.stderr.decode(errors="replace")
    input_format = _INPUT_FORMAT_RE.search(stderr)
    container_formats = frozenset(input_format.group(1).split(",")) if input_format else frozenset()
    banner = _parse_banner(stderr)
    try:
        accumulators = _parse_framecrc(result.stdout.decode(errors="replace"))
    except (ValueError, ZeroDivisionError) as exc:
        raise MediaProbeError(f"无法解析 ffmpeg 输出：{path.name}") from exc
    return _build_probe(container_formats, accumulators, banner)


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
