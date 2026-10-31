"""成片内容信号告警：在联系表的画面信号（黑屏 / 卡帧）上按阈值复核，再检测音轨静默段，生成不阻断出片的告警。

长黑屏段、长卡帧段各记一条告警；静默段须落在预期有声窗口（原声片段、旁白、BGM）内才告警，
无预期有声的成片本就无声，不告警。检测失败或无信号只是这一版没有告警，不阻断出片。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lib.infra.ffmpeg import ffmpeg_executable, local_file_input
from lib.infra.subprocess_deadline import SubprocessDeadlineExceeded, run_with_deadline
from lib.video_review.signals import TimeSpan, VideoSignals

logger = logging.getLogger(__name__)

BLACK_WARN_SECONDS = 2.0
"""黑屏段超过这个时长记黑场告警。"""
FREEZE_WARN_SECONDS = 3.0
"""卡帧段超过这个时长记卡帧告警；比时间线级「定格过长」（2 秒）略长，有意的定格不重复告警。"""
SILENCE_WARN_SECONDS = 3.0
"""静默段超过这个时长、且落在预期有声窗口内时记静音告警。"""
SILENCE_NOISE_DB = -40
"""静默判定的噪声底（dBFS），低于该响度视为无声。"""

_DETECT_DEADLINE_SECONDS = 120.0
_TIME_EPSILON = 1e-4

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


class SignalWarningKind(StrEnum):
    BLACK = "black"
    FREEZE = "freeze"
    SILENCE = "silence"


class FinalCutSignalWarning(BaseModel):
    """一条超出阈值的成片内容信号段；``[start, end)`` 以秒计，只提示、不阻断。"""

    model_config = ConfigDict(frozen=True)

    kind: SignalWarningKind
    start: float
    end: float


def _long_enough(span: TimeSpan, minimum_seconds: float) -> bool:
    return span.end - span.start >= minimum_seconds - _TIME_EPSILON


def _overlaps(first: TimeSpan, second: TimeSpan) -> bool:
    return first.start < second.end - _TIME_EPSILON and second.start < first.end - _TIME_EPSILON


def content_signal_warnings(
    signals: VideoSignals,
    silence: Sequence[TimeSpan],
    expected_audio: Sequence[TimeSpan] = (),
) -> tuple[FinalCutSignalWarning, ...]:
    """按阈值复核画面信号与静默段，返回按起点排序的告警。"""
    warnings = [
        FinalCutSignalWarning(kind=SignalWarningKind.BLACK, start=span.start, end=span.end)
        for span in signals.black
        if _long_enough(span, BLACK_WARN_SECONDS)
    ]
    warnings += [
        FinalCutSignalWarning(kind=SignalWarningKind.FREEZE, start=span.start, end=span.end)
        for span in signals.freeze
        if _long_enough(span, FREEZE_WARN_SECONDS)
    ]
    warnings += [
        FinalCutSignalWarning(kind=SignalWarningKind.SILENCE, start=span.start, end=span.end)
        for span in silence
        if _long_enough(span, SILENCE_WARN_SECONDS) and any(_overlaps(span, expected) for expected in expected_audio)
    ]
    return tuple(sorted(warnings, key=lambda warning: (warning.start, warning.end)))


def _parse_silence(log: str, duration_seconds: float) -> tuple[TimeSpan, ...]:
    spans: list[TimeSpan] = []
    start: float | None = None
    for line in log.splitlines():
        if (match := _SILENCE_START_RE.search(line)) is not None:
            start = float(match.group(1))
        elif (match := _SILENCE_END_RE.search(line)) is not None and start is not None:
            end = float(match.group(1))
            spans.append(TimeSpan(start, duration_seconds if end < 0 else end))
            start = None
    if start is not None:
        # 视频结束时仍在静默：ffmpeg 不再补发 silence_end，静默延续到末尾。
        spans.append(TimeSpan(start, duration_seconds))
    return tuple(TimeSpan(round(span.start, 3), round(span.end, 3)) for span in spans)


async def detect_silence_spans(video: Path, *, duration_seconds: float) -> tuple[TimeSpan, ...]:
    """检测音轨里达到 :data:`SILENCE_WARN_SECONDS` 的静默段（best-effort）；没有音轨、失败或超时都返回空。"""
    args = [
        ffmpeg_executable(),
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-v",
        "info",
        *local_file_input(video),
        "-map",
        "0:a:0",
        "-af",
        f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_WARN_SECONDS}",
        "-f",
        "null",
        "-",
    ]
    try:
        result = await run_with_deadline(args, deadline_seconds=_DETECT_DEADLINE_SECONDS, capture_stderr=True)
    except SubprocessDeadlineExceeded:
        logger.warning("成片静音段检测超时：%s", video.name)
        return ()
    if result.returncode != 0:
        logger.warning("成片静音段检测失败（无音轨或解码失败）：%s", video.name)
        return ()
    return _parse_silence(result.stderr.decode(errors="replace"), duration_seconds)


__all__ = [
    "BLACK_WARN_SECONDS",
    "FREEZE_WARN_SECONDS",
    "SILENCE_NOISE_DB",
    "SILENCE_WARN_SECONDS",
    "FinalCutSignalWarning",
    "SignalWarningKind",
    "content_signal_warnings",
    "detect_silence_spans",
]
