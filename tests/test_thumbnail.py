import asyncio
import shutil
from pathlib import Path

import pytest

from lib.thumbnail import extract_video_thumbnail


class TestExtractVideoThumbnail:
    @pytest.fixture(autouse=True)
    def check_ffmpeg(self):
        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg not available")

    async def test_extracts_thumbnail_from_video(self, tmp_path):
        video_path = tmp_path / "test.mp4"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
            "-c:v", "libx264", "-t", "1", "-y", str(video_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        assert video_path.exists()

        thumbnail_path = tmp_path / "thumb.jpg"
        result = await extract_video_thumbnail(video_path, thumbnail_path)
        assert result == thumbnail_path
        assert thumbnail_path.exists()
        assert thumbnail_path.stat().st_size > 0

    async def test_creates_parent_directory(self, tmp_path):
        video_path = tmp_path / "test.mp4"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1",
            "-c:v", "libx264", "-t", "1", "-y", str(video_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        thumbnail_path = tmp_path / "sub" / "dir" / "thumb.jpg"
        result = await extract_video_thumbnail(video_path, thumbnail_path)
        assert result == thumbnail_path
        assert thumbnail_path.exists()

    async def test_returns_none_for_missing_video(self, tmp_path):
        result = await extract_video_thumbnail(
            tmp_path / "missing.mp4", tmp_path / "thumb.jpg"
        )
        assert result is None

    async def test_returns_none_when_ffmpeg_fails(self, tmp_path):
        bad_video = tmp_path / "bad.mp4"
        bad_video.write_text("not a video")
        result = await extract_video_thumbnail(bad_video, tmp_path / "thumb.jpg")
        assert result is None
