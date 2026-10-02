"""联系表：对随包 ffmpeg 现场合成的分色素材断言帧数、尺寸上限与时间码。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from lib.video_review.contact_sheet import (
    MAX_FRAMES_PER_SHEET,
    MAX_SHEET_EDGE,
    build_contact_sheets,
)
from tests.factories import run_bundled_ffmpeg

_SEGMENT_COLORS = (("red", (255, 0, 0)), ("lime", (0, 255, 0)), ("blue", (0, 0, 255)))


def _color_segments(path: Path, *, size: str = "320x180", fps: int = 25) -> Path:
    """每秒一种纯色：0–1 s 红、1–2 s 绿、2–3 s 蓝。"""
    sources = ";".join(
        f"color={name}:size={size}:rate={fps}:duration=1[s{index}]"
        for index, (name, _rgb) in enumerate(_SEGMENT_COLORS)
    )
    run_bundled_ffmpeg(
        "-filter_complex",
        f"{sources};[s0][s1][s2]concat=n=3:v=1:a=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    )
    return path


def _decoded(jpeg: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(jpeg))
    image.load()
    return image.convert("RGB")


def _expected_color(time_seconds: float) -> tuple[int, int, int]:
    return _SEGMENT_COLORS[min(int(time_seconds), 2)][1]


def _close(actual: tuple[int, ...], expected: tuple[int, int, int]) -> bool:
    return all(abs(a - e) <= 40 for a, e in zip(actual, expected, strict=True))


async def test_each_tile_shows_the_frame_at_its_labelled_time(tmp_path: Path):
    video = _color_segments(tmp_path / "clip.mp4")

    sheets = await build_contact_sheets(video, unit_id="E1S01", version=2, frames=9)

    assert len(sheets) == 1
    sheet = sheets[0]
    assert (sheet.unit_id, sheet.version) == ("E1S01", 2)
    assert len(sheet.frames) == 9
    image = _decoded(sheet.jpeg)
    times = [frame.time_seconds for frame in sheet.frames]
    assert times == sorted(times)
    assert times[0] < 1
    assert times[-1] >= 2
    for frame in sheet.frames:
        # 时间码落在 25 fps 的帧格上：标注的是该帧自身的起点。
        assert frame.time_seconds * 25 == pytest.approx(round(frame.time_seconds * 25), abs=1e-6)
        left, top, right, bottom = frame.box
        center = image.getpixel(((left + right) // 2, (top + bottom) // 2))
        assert isinstance(center, tuple)
        assert _close(center, _expected_color(frame.time_seconds)), (frame, center)


async def test_frames_beyond_one_sheet_spill_into_the_next(tmp_path: Path):
    video = _color_segments(tmp_path / "clip.mp4")

    sheets = await build_contact_sheets(video, unit_id="E1S01", version=1, frames=20)

    assert [len(sheet.frames) for sheet in sheets] == [MAX_FRAMES_PER_SHEET, 20 - MAX_FRAMES_PER_SHEET]
    times = [frame.time_seconds for sheet in sheets for frame in sheet.frames]
    assert times == sorted(set(times))


@pytest.mark.parametrize("size", ["1920x1080", "1080x1920"])
async def test_a_full_sheet_of_large_frames_stays_within_the_edge_limit(tmp_path: Path, size: str):
    video = _color_segments(tmp_path / "clip.mp4", size=size)

    sheets = await build_contact_sheets(video, unit_id="E1U1", version=1, frames=MAX_FRAMES_PER_SHEET)

    image = _decoded(sheets[0].jpeg)
    assert max(image.size) <= MAX_SHEET_EDGE
    assert (sheets[0].width, sheets[0].height) == image.size
    assert len(sheets[0].frames) == MAX_FRAMES_PER_SHEET


async def test_requesting_more_frames_than_the_video_has_uses_every_frame_once(tmp_path: Path):
    video = tmp_path / "short.mp4"
    run_bundled_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=red:size=64x64:rate=25:duration=0.2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(video),
    )

    sheets = await build_contact_sheets(video, unit_id="E1S02", version=1, frames=12)

    assert [frame.time_seconds for frame in sheets[0].frames] == pytest.approx([0.0, 0.04, 0.08, 0.12, 0.16])
