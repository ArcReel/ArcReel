"""成片联系表：抽成片文件的帧拼成一张带标注的图，出片后 best-effort 生成。

复用 :mod:`lib.video_review` 的抽帧、排版与信号标记（黑屏 / 卡帧 / 切点）：帧点按镜头切点优先、
均匀补点（:func:`lib.video_review.sampling.plan_frame_indices` 已有策略）。探测帧时刻与画面信号
（:func:`load_sheet_materials`）同时是内容信号告警（:mod:`lib.final_cut.content_signals`）的输入。
生成失败不阻断出片，只是这一版成片没有联系表。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from lib.infra.ffmpeg import FfmpegUnavailableError
from lib.infra.json_io import atomic_write_bytes
from lib.infra.media_probe import MediaProbeError, probe_video_frame_times
from lib.video_review.contact_sheet import MAX_FRAMES_PER_SHEET, ContactSheetError, build_contact_sheets
from lib.video_review.signals import SignalDetectionError, VideoSignals, detect_signals

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SheetMaterials:
    """成片文件的帧时刻与画面信号：联系表与内容信号告警的共用输入。"""

    frame_times: tuple[float, ...]
    signals: VideoSignals


async def load_sheet_materials(video: Path) -> SheetMaterials | None:
    """探测帧时刻并检测画面信号（best-effort）；失败只记日志并返回 None。"""
    try:
        frame_times = await probe_video_frame_times(video)
        signals = await detect_signals(video, frame_times)
    except (MediaProbeError, SignalDetectionError, FfmpegUnavailableError, OSError):
        logger.warning("成片画面信号检测失败：%s", video, exc_info=True)
        return None
    return SheetMaterials(frame_times=tuple(frame_times), signals=signals)


async def build_final_cut_sheet(
    video: Path,
    sheet_file: Path,
    *,
    label: str,
    version: int,
    materials: SheetMaterials,
    frames: int = MAX_FRAMES_PER_SHEET,
) -> bool:
    """按 ``materials`` 抽约 ``frames`` 帧拼成一张联系表写到 ``sheet_file``；成功返回 True，失败只记日志并返回 False。"""
    try:
        sheets = await build_contact_sheets(
            video,
            unit_id=label,
            version=version,
            frames=frames,
            frame_times=materials.frame_times,
            signals=materials.signals,
        )
    except (ContactSheetError, FfmpegUnavailableError, OSError):
        logger.warning("成片联系表生成失败：%s", video, exc_info=True)
        return False
    try:
        await asyncio.to_thread(atomic_write_bytes, sheet_file, sheets[0].jpeg)
    except OSError:
        logger.warning("成片联系表写入失败：%s", sheet_file, exc_info=True)
        return False
    return True


__all__ = ["SheetMaterials", "build_final_cut_sheet", "load_sheet_materials"]
