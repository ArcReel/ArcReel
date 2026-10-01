"""联系表：把一个视频版本按时间均匀抽出的若干帧拼成带标注的图片。

每张联系表最多 :data:`MAX_FRAMES_PER_SHEET` 帧，长边不超过 :data:`MAX_SHEET_EDGE` px；
抽出的帧超过一张的容量时依次分到后续几张。顶部标注视频单元 ID 与版本号，每帧下方标注视频单元 ID
与该帧的起始时刻（秒，以首帧为 0，与剪辑时间线的入出点同一时间轴）。

抽帧按帧索引精确定位：先只解复用读出逐帧时刻，再一次解码用 ``select`` 取出选中的帧，
因此标注的时刻就是画面那一帧自身的起点。
"""

from __future__ import annotations

import asyncio
import io
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from lib.infra.ffmpeg import ffmpeg_executable, local_file_input
from lib.infra.media_probe import probe_video_frame_times
from lib.infra.subprocess_deadline import SubprocessDeadlineExceeded, run_with_deadline

MAX_FRAMES_PER_SHEET = 12
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


class ContactSheetError(RuntimeError):
    """抽帧失败或超时。"""


@dataclass(frozen=True, slots=True)
class SheetFrame:
    time_seconds: float
    """该帧的起始时刻（秒），以首帧为 0。"""
    box: tuple[int, int, int, int]
    """该帧画面在联系表上的位置（left, top, right, bottom）。"""


@dataclass(frozen=True, slots=True)
class ContactSheet:
    unit_id: str
    version: int
    frames: tuple[SheetFrame, ...]
    jpeg: bytes
    width: int
    height: int


def sample_frame_indices(frame_count: int, count: int) -> list[int]:
    """在 ``frame_count`` 帧里均匀取 ``count`` 帧（各取所在等分区间的中点）；帧数不够时全取。"""
    if count >= frame_count:
        return list(range(frame_count))
    return [math.floor((index + 0.5) * frame_count / count) for index in range(count)]


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


def _layout(count: int, aspect: float) -> tuple[int, int, int]:
    """为 ``count`` 帧挑列数，使联系表长边不超上限时单帧面积最大；返回 (列数, 帧宽, 帧高)。"""
    best: tuple[int, int, int] | None = None
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        width_room = (MAX_SHEET_EDGE - _PADDING * (cols + 1)) / cols
        height_room = (MAX_SHEET_EDGE - _HEADER_HEIGHT - _PADDING * (rows + 1)) / rows - _LABEL_HEIGHT
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
    images: list[Image.Image], times: list[float], *, unit_id: str, version: int, sheet_label: str
) -> ContactSheet:
    aspect = images[0].width / images[0].height
    cols, tile_width, tile_height = _layout(len(images), aspect)
    rows = math.ceil(len(images) / cols)
    width = cols * tile_width + _PADDING * (cols + 1)
    height = _HEADER_HEIGHT + rows * (tile_height + _LABEL_HEIGHT) + _PADDING * (rows + 1)
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
    for position, (image, time_seconds) in enumerate(zip(images, times, strict=True)):
        row, col = divmod(position, cols)
        left = _PADDING + col * (tile_width + _PADDING)
        top = _HEADER_HEIGHT + _PADDING + row * (tile_height + _LABEL_HEIGHT + _PADDING)
        sheet.paste(image.resize((tile_width, tile_height), Image.Resampling.LANCZOS), (left, top))
        draw.text(
            (left, top + tile_height + (_LABEL_HEIGHT - _LABEL_FONT_SIZE) // 2),
            f"{unit_id}  {time_seconds:.3f}s",
            fill=_TEXT,
            font=label_font,
        )
        frames.append(SheetFrame(time_seconds=time_seconds, box=(left, top, left + tile_width, top + tile_height)))
    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    return ContactSheet(
        unit_id=unit_id, version=version, frames=tuple(frames), jpeg=buffer.getvalue(), width=width, height=height
    )


async def build_contact_sheets(
    video_path: Path, *, unit_id: str, version: int, frames: int
) -> tuple[ContactSheet, ...]:
    """为一个视频版本均匀抽 ``frames`` 帧（视频帧数更少时全取），按顺序拼成一张或几张联系表。

    Raises:
        FfmpegUnavailableError: 随包 ffmpeg 不可用。
        MediaProbeError: 视频无法解析或没有视频帧。
        ContactSheetError: 抽帧失败或超时。
    """
    times = await probe_video_frame_times(video_path)
    indices = sample_frame_indices(len(times), frames)
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
    "ContactSheet",
    "ContactSheetError",
    "SheetFrame",
    "build_contact_sheets",
    "sample_frame_indices",
]
