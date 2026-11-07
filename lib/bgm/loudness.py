"""Measure EBU R128 integrated audio loudness using bundled ffmpeg’s ``ebur128`` filter."""

from __future__ import annotations

import re
from pathlib import Path

from lib.infra.ffmpeg import local_file_input
from lib.infra.subprocess_deadline import Spawner, SubprocessDeadlineExceeded, run_with_deadline

MEASURE_DEADLINE_FLOOR_SECONDS = 60.0
MEASURE_DEADLINE_PER_AUDIO_SECOND = 0.5

_INTEGRATED_RE = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?\d+(?:\.\d+)?|-inf)\s*LUFS")


class LoudnessMeasurementError(Exception):
    """ffmpeg cannot measure integrated loudness: decoding failure, timeout, or missing ``ebur128`` summary."""


def measure_args(ffmpeg: str, path: Path) -> list[str]:
    """``ebur128`` prints its summary only at completion; disable per-frame logs and emit the summary to stderr at info level."""
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
    """Read integrated loudness (LUFS) from ``ebur128`` summaries; ffmpeg reports silence as −70 or ``-inf``, both treated as at/below −70."""
    matches = _INTEGRATED_RE.findall(stderr)
    if not matches:
        raise LoudnessMeasurementError("ffmpeg 输出里没有积分响度汇总")
    value = matches[-1]
    return float("-inf") if value == "-inf" else float(value)


async def measure_integrated_loudness(
    ffmpeg: str, path: Path, *, duration_seconds: float, spawn: Spawner | None = None
) -> float:
    """Measure integrated loudness (LUFS) for ``path``."""
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
