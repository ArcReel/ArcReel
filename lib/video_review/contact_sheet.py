"""联系表：把一个视频版本抽出的若干帧拼成带标注的图片。

每张联系表最多 :data:`MAX_FRAMES_PER_SHEET` 帧，长边不超过 :data:`MAX_SHEET_EDGE` px；
抽出的帧超过一张的容量时依次分到后续几张。顶部标注视频单元 ID 与版本号，每帧下方标注视频单元 ID
与该帧的起始时刻（秒，以首帧为 0，与剪辑时间线的入出点同一时间轴）；帧落在黑屏段、卡帧段或是镜头首帧时，
时刻下方再标 BLACK / FREEZE / CUT。抽帧计划见 ``lib.video_review.sampling``。

抽帧按帧索引精确定位：先只解复用读出逐帧时刻，再一次解码用 ``select`` 取出选中的帧，
因此标注的时刻就是画面那一帧自身的起点。
"""

from __future__ import annotations

import asyncio
import io
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from lib.infra.ffmpeg import ffmpeg_executable, local_file_input
from lib.infra.media_probe import probe_video_frame_times
from lib.infra.subprocess_deadline import SubprocessDeadlineExceeded, run_with_deadline
from lib.video_review.sampling import plan_frame_indices
from lib.video_review.signals import TAG_BLACK, TAG_CUT, TAG_FREEZE, VideoSignals

MAX_FRAMES_PER_SHEET = 12
MAX_SHOT_FRAMES = 24
MAX_SHEET_EDGE = 2000

_EXTRACT_DEADLINE_SECONDS = 120.0
# 抽出的帧先缩到这个长边再拼版：单帧铺满整张联系表时也不超过上限。
_FRAME_EDGE = 1280
_PNG_END = b"IEND\xaeB`\x82"
_PADDING = 8
_HEADER_HEIGHT = 40
_LABEL_HEIGHT = 32
_HEADER_FONT_SIZE = 26
_LABEL_FONT_SIZE = 22
_JPEG_QUALITY = 85
_BACKGROUND = (24, 24, 24)
_TEXT = (240, 240, 240)
_TAG_COLORS = {TAG_CUT: (90, 200, 255), TAG_BLACK: (255, 150, 60), TAG_FREEZE: (255, 100, 200)}


class ContactSheetError(RuntimeError):
    """抽帧失败或超时。"""


@dataclass(frozen=True, slots=True)
class SheetFrame:
    time_seconds: float
    """该帧的起始时刻（秒），以首帧为 0。"""
    box: tuple[int, int, int, int]
    """该帧画面在联系表上的位置（left, top, right, bottom）。"""
    tags: tuple[str, ...] = ()
    """该帧所在的信号标记（CUT / BLACK / FREEZE）。"""


@dataclass(frozen=True, slots=True)
class ContactSheet:
    unit_id: str
    version: int
    frames: tuple[SheetFrame, ...]
    jpeg: bytes
    width: int
    height: int


async def _extract_frames(video_path: Path, indices: list[int]) -> list[Image.Image]:
    selector = "+".join(f"eq(n\\,{index})" for index in indices)
    scale = f"scale='if(gte(iw,ih),min({_FRAME_EDGE},iw),-2)':'if(gte(iw,ih),-2,min({_FRAME_EDGE},ih))'"
    args = [
        ffmpeg_executable(),
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-v",
        "error",
        *local_file_input(video_path),
        "-map",
        "0:v:0",
        "-vf",
        f"select='{selector}',{scale}",
        "-fps_mode",
        "passthrough",
        "-f",
        "image2pipe",
        "-c:v",
        "png",
        "-",
    ]
    try:
        result = await run_with_deadline(args, deadline_seconds=_EXTRACT_DEADLINE_SECONDS, capture_stdout=True)
    except SubprocessDeadlineExceeded:
        raise ContactSheetError(f"抽帧超时：{video_path.name}") from None
    if result.returncode != 0:
        raise ContactSheetError(f"抽帧失败：{video_path.name}")
    chunks = [chunk + _PNG_END for chunk in result.stdout.split(_PNG_END) if chunk]
    if len(chunks) != len(indices):
        raise ContactSheetError(f"抽帧数量不符：{video_path.name} 期望 {len(indices)} 帧，得到 {len(chunks)} 帧")
    frames: list[Image.Image] = []
    for chunk in chunks:
        image = Image.open(io.BytesIO(chunk))
        image.load()
        frames.append(image.convert("RGB"))
    return frames


def _layout(count: int, aspect: float, label_height: int) -> tuple[int, int, int]:
    """为 ``count`` 帧挑列数，使联系表长边不超上限时单帧面积最大；返回 (列数, 帧宽, 帧高)。"""
    best: tuple[int, int, int] | None = None
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        width_room = (MAX_SHEET_EDGE - _PADDING * (cols + 1)) / cols
        height_room = (MAX_SHEET_EDGE - _HEADER_HEIGHT - _PADDING * (rows + 1)) / rows - label_height
        tile_width = math.floor(min(width_room, height_room * aspect))
        tile_height = math.floor(tile_width / aspect)
        if tile_width < 1 or tile_height < 1:
            continue
        if best is None or tile_width * tile_height > best[1] * best[2]:
            best = (cols, tile_width, tile_height)
    if best is None:
        raise ContactSheetError("画面比例过于极端，无法排版联系表")
    return best


def _compose(
    images: list[Image.Image],
    times: list[float],
    tags: list[tuple[str, ...]],
    *,
    unit_id: str,
    version: int,
    sheet_label: str,
) -> ContactSheet:
    aspect = images[0].width / images[0].height
    label_height = _LABEL_HEIGHT * (2 if any(tags) else 1)
    cols, tile_width, tile_height = _layout(len(images), aspect, label_height)
    rows = math.ceil(len(images) / cols)
    width = cols * tile_width + _PADDING * (cols + 1)
    height = _HEADER_HEIGHT + rows * (tile_height + label_height) + _PADDING * (rows + 1)
    sheet = Image.new("RGB", (width, height), _BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    header_font = ImageFont.load_default(size=_HEADER_FONT_SIZE)
    label_font = ImageFont.load_default(size=_LABEL_FONT_SIZE)
    draw.text(
        (_PADDING, (_HEADER_HEIGHT - _HEADER_FONT_SIZE) // 2),
        f"{unit_id}  v{version}  {sheet_label}",
        fill=_TEXT,
        font=header_font,
    )
    frames: list[SheetFrame] = []
    for position, (image, time_seconds, frame_tags) in enumerate(zip(images, times, tags, strict=True)):
        row, col = divmod(position, cols)
        left = _PADDING + col * (tile_width + _PADDING)
        top = _HEADER_HEIGHT + _PADDING + row * (tile_height + label_height + _PADDING)
        sheet.paste(image.resize((tile_width, tile_height), Image.Resampling.LANCZOS), (left, top))
        text_top = top + tile_height + (_LABEL_HEIGHT - _LABEL_FONT_SIZE) // 2
        draw.text((left, text_top), f"{unit_id}  {time_seconds:.3f}s", fill=_TEXT, font=label_font)
        tag_left = left
        for tag in frame_tags:
            draw.text((tag_left, text_top + _LABEL_HEIGHT), tag, fill=_TAG_COLORS[tag], font=label_font)
            tag_left += math.ceil(draw.textlength(tag, font=label_font)) + _PADDING
        frames.append(
            SheetFrame(
                time_seconds=time_seconds, box=(left, top, left + tile_width, top + tile_height), tags=frame_tags
            )
        )
    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    return ContactSheet(
        unit_id=unit_id, version=version, frames=tuple(frames), jpeg=buffer.getvalue(), width=width, height=height
    )


async def build_contact_sheets(
    video_path: Path,
    *,
    unit_id: str,
    version: int,
    frames: int,
    frame_times: Sequence[float] | None = None,
    signals: VideoSignals | None = None,
) -> tuple[ContactSheet, ...]:
    """为一个视频版本抽约 ``frames`` 帧，按顺序拼成一张或几张联系表。

    抽帧按 :func:`lib.video_review.sampling.plan_frame_indices` 计划：有 ``signals`` 时每个镜头至少一帧、
    信号两侧加密，镜头数超过 ``frames`` 时总帧数提到镜头数（不超过 :data:`MAX_SHOT_FRAMES`）；
    视频帧数更少时全取。``frame_times`` 缺省时现场探测。

    Raises:
        FfmpegUnavailableError: 随包 ffmpeg 不可用。
        MediaProbeError: 视频无法解析或没有视频帧。
        ContactSheetError: 抽帧失败或超时。
    """
    times = frame_times if frame_times is not None else await probe_video_frame_times(video_path)
    indices = plan_frame_indices(times, signals, frames, max_count=MAX_SHOT_FRAMES)
    images = await _extract_frames(video_path, indices)
    chunks = [
        (
            images[start : start + MAX_FRAMES_PER_SHEET],
            [times[index] for index in indices[start : start + MAX_FRAMES_PER_SHEET]],
        )
        for start in range(0, len(indices), MAX_FRAMES_PER_SHEET)
    ]
    sheets = [
        await asyncio.to_thread(
            _compose,
            chunk_images,
            chunk_times,
            [signals.tags_at(time_seconds) if signals is not None else () for time_seconds in chunk_times],
            unit_id=unit_id,
            version=version,
            sheet_label=f"{number}/{len(chunks)}",
        )
        for number, (chunk_images, chunk_times) in enumerate(chunks, start=1)
    ]
    return tuple(sheets)


__all__ = [
    "MAX_FRAMES_PER_SHEET",
    "MAX_SHEET_EDGE",
    "MAX_SHOT_FRAMES",
    "ContactSheet",
    "ContactSheetError",
    "SheetFrame",
    "build_contact_sheets",
]
