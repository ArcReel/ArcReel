"""用随包 ffmpeg 的 ``ebur128`` 滤镜实测音频的 EBU R128 积分响度。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lib.infra.ffmpeg import local_file_input
from lib.infra.subprocess_deadline import Spawner, SubprocessDeadlineExceeded, run_with_deadline

MEASURE_DEADLINE_FLOOR_SECONDS = 60.0
MEASURE_DEADLINE_PER_AUDIO_SECOND = 0.5

_INTEGRATED_RE = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?\d+(?:\.\d+)?|-inf)\s*LUFS")
_THRESHOLD_RE = re.compile(r"^\s*Threshold:\s*(-?\d+(?:\.\d+)?|-inf)\s*LUFS", re.MULTILINE)
_LRA_RE = re.compile(r"^\s*LRA:\s*(-?\d+(?:\.\d+)?)\s*LU", re.MULTILINE)
_PEAK_RE = re.compile(r"^\s*Peak:\s*(-?\d+(?:\.\d+)?|-inf)\s*dBFS", re.MULTILINE)


class LoudnessMeasurementError(Exception):
    """ffmpeg 测不出积分响度：解码失败、超时，或输出里没有 ``ebur128`` 的汇总。"""


@dataclass(frozen=True, slots=True)
class LoudnessSummary:
    """``ebur128`` 的汇总段：积分响度必有，其余只在汇总里给出时非 ``None``。"""

    integrated_lufs: float
    true_peak_dbtp: float | None = None
    lra_lu: float | None = None
    threshold_lufs: float | None = None


def _as_float(value: str) -> float:
    return float("-inf") if value == "-inf" else float(value)


def measure_args(ffmpeg: str, path: Path, *, true_peak: bool = False) -> list[str]:
    """``ebur128`` 只在结束时打印汇总；逐帧日志关闭，汇总按 info 级别写到 stderr。"""
    filter_spec = "ebur128=framelog=quiet" + (":peak=true" if true_peak else "")
    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        *local_file_input(path),
        "-vn",
        "-af",
        filter_spec,
        "-f",
        "null",
        "-",
    ]


def parse_loudness_summary(stderr: str) -> LoudnessSummary:
    """解析 ``ebur128`` 的汇总段；没有汇总时记为测量失败。

    真峰只在以 ``peak=true`` 实测时出现；门限取积分响度段里的第一条（汇总后两段各自的
    Threshold 单位不是 LUFS，不会被误取）。
    """
    matches = _INTEGRATED_RE.findall(stderr)
    if not matches:
        raise LoudnessMeasurementError("ffmpeg 输出里没有积分响度汇总")
    threshold = _THRESHOLD_RE.search(stderr)
    lra = _LRA_RE.search(stderr)
    peak = _PEAK_RE.search(stderr)
    return LoudnessSummary(
        integrated_lufs=_as_float(matches[-1]),
        true_peak_dbtp=_as_float(peak.group(1)) if peak is not None else None,
        lra_lu=float(lra.group(1)) if lra is not None else None,
        threshold_lufs=_as_float(threshold.group(1)) if threshold is not None else None,
    )


def parse_integrated_loudness(stderr: str) -> float:
    """从 ``ebur128`` 的汇总里取积分响度（LUFS）；无声时 ffmpeg 报 −70 或 ``-inf``，都记为 −70 以下。"""
    return parse_loudness_summary(stderr).integrated_lufs


async def measure_loudness(
    ffmpeg: str,
    path: Path,
    *,
    duration_seconds: float,
    spawn: Spawner | None = None,
    true_peak: bool = False,
) -> LoudnessSummary:
    """实测 ``path`` 的 EBU R128 汇总；``true_peak`` 为真时连真峰一起实测。"""
    try:
        result = await run_with_deadline(
            measure_args(ffmpeg, path, true_peak=true_peak),
            deadline_seconds=MEASURE_DEADLINE_FLOOR_SECONDS + MEASURE_DEADLINE_PER_AUDIO_SECOND * duration_seconds,
            capture_stderr=True,
            spawn=spawn,
        )
    except SubprocessDeadlineExceeded:
        raise LoudnessMeasurementError("响度测量超时") from None
    stderr = result.stderr.decode(errors="replace")
    if result.returncode != 0:
        raise LoudnessMeasurementError(f"ffmpeg 退出码 {result.returncode}：{stderr.strip()[-500:]}")
    return parse_loudness_summary(stderr)


async def measure_integrated_loudness(
    ffmpeg: str, path: Path, *, duration_seconds: float, spawn: Spawner | None = None
) -> float:
    """实测 ``path`` 的积分响度（LUFS）。"""
    return (await measure_loudness(ffmpeg, path, duration_seconds=duration_seconds, spawn=spawn)).integrated_lufs


__all__ = [
    "LoudnessMeasurementError",
    "LoudnessSummary",
    "measure_args",
    "measure_integrated_loudness",
    "measure_loudness",
    "parse_integrated_loudness",
    "parse_loudness_summary",
]
