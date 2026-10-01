"""用随包 ffmpeg 的 ``ebur128`` 滤镜实测音频的 EBU R128 积分响度。"""

from __future__ import annotations

import re
from pathlib import Path

from lib.infra.ffmpeg import local_file_input
from lib.infra.subprocess_deadline import Spawner, SubprocessDeadlineExceeded, run_with_deadline

MEASURE_DEADLINE_FLOOR_SECONDS = 60.0
MEASURE_DEADLINE_PER_AUDIO_SECOND = 0.5

_INTEGRATED_RE = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?\d+(?:\.\d+)?|-inf)\s*LUFS")


class LoudnessMeasurementError(Exception):
    """ffmpeg 测不出积分响度：解码失败、超时，或输出里没有 ``ebur128`` 的汇总。"""


def measure_args(ffmpeg: str, path: Path) -> list[str]:
    """``ebur128`` 只在结束时打印汇总；逐帧日志关闭，汇总按 info 级别写到 stderr。"""
    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        *local_file_input(path),
        "-vn",
        "-af",
        "ebur128=framelog=quiet",
        "-f",
        "null",
        "-",
    ]


def parse_integrated_loudness(stderr: str) -> float:
    """从 ``ebur128`` 的汇总里取积分响度（LUFS）；无声时 ffmpeg 报 −70 或 ``-inf``，都记为 −70 以下。"""
    matches = _INTEGRATED_RE.findall(stderr)
    if not matches:
        raise LoudnessMeasurementError("ffmpeg 输出里没有积分响度汇总")
    value = matches[-1]
    return float("-inf") if value == "-inf" else float(value)


async def measure_integrated_loudness(
    ffmpeg: str, path: Path, *, duration_seconds: float, spawn: Spawner | None = None
) -> float:
    """实测 ``path`` 的积分响度（LUFS）。"""
    try:
        result = await run_with_deadline(
            measure_args(ffmpeg, path),
            deadline_seconds=MEASURE_DEADLINE_FLOOR_SECONDS + MEASURE_DEADLINE_PER_AUDIO_SECOND * duration_seconds,
            capture_stderr=True,
            spawn=spawn,
        )
    except SubprocessDeadlineExceeded:
        raise LoudnessMeasurementError("响度测量超时") from None
    stderr = result.stderr.decode(errors="replace")
    if result.returncode != 0:
        raise LoudnessMeasurementError(f"ffmpeg 退出码 {result.returncode}：{stderr.strip()[-500:]}")
    return parse_integrated_loudness(stderr)


__all__ = [
    "LoudnessMeasurementError",
    "measure_args",
    "measure_integrated_loudness",
    "parse_integrated_loudness",
]
