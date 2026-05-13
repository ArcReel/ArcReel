"""Thumbnail extraction graceful-skip when ffmpeg is unavailable."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import lib.thumbnail as thumbnail_module


@pytest.fixture(autouse=True)
def _reset_ffmpeg_cache():
    thumbnail_module._reset_for_tests()
    yield
    thumbnail_module._reset_for_tests()


@pytest.mark.asyncio
async def test_returns_none_when_ffmpeg_missing(tmp_path: Path):
    """ffmpeg 不在 PATH 中时不应 spawn 子进程，直接返回 None。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")  # nominal file; we never actually decode
    out = tmp_path / "out.jpg"

    with patch("lib.thumbnail.shutil.which", return_value=None):
        with patch("lib.thumbnail.asyncio.create_subprocess_exec") as spawn:
            result = await thumbnail_module.extract_video_thumbnail(video, out)

    assert result is None
    assert not out.exists()
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_returns_none_when_video_missing(tmp_path: Path):
    """video 文件不存在时直接返回 None，不检查 ffmpeg。"""
    nonexistent = tmp_path / "no-such-video.mp4"
    out = tmp_path / "out.jpg"

    with patch("lib.thumbnail.shutil.which") as which:
        result = await thumbnail_module.extract_video_thumbnail(nonexistent, out)

    assert result is None
    which.assert_not_called()


@pytest.mark.asyncio
async def test_ffmpeg_available_attempts_extraction(tmp_path: Path):
    """ffmpeg 在 PATH 时走原有 spawn 路径（spawn 被调用，returncode 非零仍返回 None）。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.jpg"

    class _FakeProc:
        returncode = 1  # ffmpeg failure

        async def wait(self):
            return None

    with patch("lib.thumbnail.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch(
            "lib.thumbnail.asyncio.create_subprocess_exec",
            return_value=_FakeProc(),
        ) as spawn:
            result = await thumbnail_module.extract_video_thumbnail(video, out)

    assert result is None
    spawn.assert_called_once()


def test_ffmpeg_available_is_cached():
    """_ffmpeg_available() 用 @functools.cache，多次调用 shutil.which 只一次。"""
    thumbnail_module._reset_for_tests()
    with patch("lib.thumbnail.shutil.which", return_value=None) as which:
        thumbnail_module._ffmpeg_available()
        thumbnail_module._ffmpeg_available()
        thumbnail_module._ffmpeg_available()
    assert which.call_count == 1
