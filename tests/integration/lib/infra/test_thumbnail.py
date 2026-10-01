"""随包 ffmpeg 真实抽取首帧缩略图与尾帧。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from lib.infra.thumbnail import extract_video_last_frame, extract_video_thumbnail
from tests.factories import run_bundled_ffmpeg


@pytest.fixture
def red_then_blue_video(tmp_path: Path) -> Path:
    """前 1 秒纯红、后 1 秒纯蓝的 64x64 视频：首帧红、尾帧蓝。"""
    path = tmp_path / "red_then_blue.mp4"
    run_bundled_ffmpeg(
        "-filter_complex",
        "color=c=red:s=64x64:d=1:r=10[a];color=c=blue:s=64x64:d=1:r=10[b];[a][b]concat=n=2:v=1:a=0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    )
    return path


def _dominant_channel(image_path: Path) -> str:
    with Image.open(image_path) as image:
        pixel = image.convert("RGB").getpixel((32, 32))
    assert isinstance(pixel, tuple)
    red, green, blue = pixel[:3]
    return max((("red", red), ("green", green), ("blue", blue)), key=lambda item: item[1])[0]


class TestExtractVideoThumbnail:
    async def test_extracts_first_frame(self, tmp_path: Path, red_then_blue_video: Path):
        thumbnail_path = tmp_path / "sub" / "dir" / "thumb.jpg"

        result = await extract_video_thumbnail(red_then_blue_video, thumbnail_path)

        assert result == thumbnail_path
        assert _dominant_channel(thumbnail_path) == "red"

    async def test_returns_none_for_missing_video(self, tmp_path: Path):
        result = await extract_video_thumbnail(tmp_path / "missing.mp4", tmp_path / "thumb.jpg")
        assert result is None

    async def test_returns_none_when_ffmpeg_fails(self, tmp_path: Path):
        bad_video = tmp_path / "bad.mp4"
        bad_video.write_text("not a video", encoding="utf-8")
        result = await extract_video_thumbnail(bad_video, tmp_path / "thumb.jpg")
        assert result is None


class TestExtractVideoLastFrame:
    async def test_extracts_last_frame(self, tmp_path: Path, red_then_blue_video: Path):
        out = tmp_path / "sub" / "dir" / "last.png"

        result = await extract_video_last_frame(red_then_blue_video, out)

        assert result == out
        assert _dominant_channel(out) == "blue"

    async def test_returns_none_for_missing_video(self, tmp_path: Path):
        result = await extract_video_last_frame(tmp_path / "missing.mp4", tmp_path / "last.png")
        assert result is None

    async def test_returns_none_for_corrupt_video(self, tmp_path: Path):
        bad_video = tmp_path / "bad.mp4"
        bad_video.write_text("not a video", encoding="utf-8")
        result = await extract_video_last_frame(bad_video, tmp_path / "last.png")
        assert result is None
