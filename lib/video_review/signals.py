"""视频版本的机器检查信号：黑屏段、卡帧段、镜头切换点。

三项信号都由随包 ffmpeg 在本地一次解码算出（``blackdetect`` / ``freezedetect`` / ``scene`` 打分），
时间以首帧为 0，与联系表标注、剪辑时间线的入出点同一时间轴。信号只作提示：黑屏与卡帧是疑似问题，
镜头切换点是结构信息，不是缺陷。

结果按视频版本缓存为 JSON 侧车文件：版本快照写入后不再变化，缓存以视频文件的大小与修改时间作指纹，
算法参数变化时 :data:`SIGNALS_SCHEMA` 加一使旧缓存失效。
"""

from __future__ import annotations

import bisect
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    """信号检测失败或超时。"""


@dataclass(frozen=True, slots=True)
class TimeSpan:
    start: float
    end: float
    """区间 [start, end)，单位秒。"""

    def contains(self, time_seconds: float) -> bool:
        return self.start - _TIME_EPSILON <= time_seconds < self.end - _TIME_EPSILON


@dataclass(frozen=True, slots=True)
class VideoSignals:
    duration_seconds: float
    """视频总时长：末帧起点加一帧时长。"""
    black: tuple[TimeSpan, ...]
    freeze: tuple[TimeSpan, ...]
    cuts: tuple[float, ...]
    """镜头切换点：新镜头首帧的起点。"""

    @property
    def shots(self) -> tuple[TimeSpan, ...]:
        starts = (0.0, *self.cuts)
        ends = (*self.cuts, self.duration_seconds)
        return tuple(TimeSpan(start, end) for start, end in zip(starts, ends, strict=True))

    def tags_at(self, time_seconds: float) -> tuple[str, ...]:
        """起点为 ``time_seconds`` 的帧落在哪些信号上；固定按切点、黑屏、卡帧排序。"""
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
        """结构不符合预期时返回 None，调用方按未命中缓存处理。"""
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
    """从 ``spans`` 里挖掉 ``covered``，剩下不足 :data:`FREEZE_MIN_SECONDS` 的碎段丢弃。"""
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
        # 视频结束时仍在卡帧：ffmpeg 不再补发 freeze_end，卡帧延续到视频末尾。
        freeze.append(TimeSpan(freeze_start, duration))
    # 黑屏必然静止：黑屏段不重复计入卡帧段。
    freeze = _without(freeze, black)
    return VideoSignals(
        duration_seconds=_rounded(duration),
        black=tuple(TimeSpan(_rounded(span.start), _rounded(span.end)) for span in black),
        freeze=tuple(TimeSpan(_rounded(span.start), _rounded(span.end)) for span in freeze),
        cuts=tuple(sorted({_rounded(cut) for cut in cuts if cut > _TIME_EPSILON})),
    )


async def detect_signals(video_path: Path, frame_times: Sequence[float]) -> VideoSignals:
    """对整段视频解码一次，算出黑屏段、卡帧段与镜头切换点。

    Args:
        frame_times: 该视频每帧的起始时刻（``probe_video_frame_times`` 的结果），用来定视频总时长。

    Raises:
        FfmpegUnavailableError: 随包 ffmpeg 不可用。
        SignalDetectionError: 解码失败或超时。
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
    """取视频版本的信号：命中 ``cache_file`` 则直接返回，否则检测并写入缓存。返回 (信号, 是否命中缓存)。

    缓存写入失败只影响下次重算，不影响本次结果。
    """
    fingerprint = _fingerprint(video_path)
    cached = load_json_or_none(cache_file)
    if isinstance(cached, dict) and cached.get("schema") == SIGNALS_SCHEMA and cached.get("video") == fingerprint:
        signals = VideoSignals.from_json(cached.get("signals"))
        if signals is not None:
            return signals, True
    signals = await detect_signals(video_path, frame_times)
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(cache_file, {"schema": SIGNALS_SCHEMA, "video": fingerprint, "signals": signals.to_json()})
    except OSError:
        logger.warning("视频信号缓存写入失败：%s", cache_file, exc_info=True)
    return signals, False


def frame_index_at(frame_times: Sequence[float], time_seconds: float) -> int:
    """起点不早于 ``time_seconds``（容差内）的第一帧的索引；越过末帧时取末帧。"""
    index = bisect.bisect_left(frame_times, time_seconds - _TIME_EPSILON)
    return min(index, len(frame_times) - 1)


def last_frame_index_before(frame_times: Sequence[float], time_seconds: float) -> int:
    """起点早于 ``time_seconds``（容差内）的最后一帧的索引；没有则为 -1。"""
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
