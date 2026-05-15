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


@pytest.mark.asyncio
async def test_last_frame_returns_none_when_ffmpeg_missing(tmp_path: Path):
    """ffmpeg 缺失时 extract_video_last_frame 直接返回 None，不 spawn 子进程。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"

    with patch("lib.thumbnail.shutil.which", return_value=None):
        with patch("lib.thumbnail.asyncio.create_subprocess_exec") as spawn:
            result = await thumbnail_module.extract_video_last_frame(video, out)

    assert result is None
    assert not out.exists()
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_last_frame_returns_none_when_only_ffprobe_missing(tmp_path: Path):
    """ffmpeg 在 PATH 但 ffprobe 缺失（精简容器场景）时也应短路。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"

    def _which(name: str):
        return "/usr/bin/ffmpeg" if name == "ffmpeg" else None

    with patch("lib.thumbnail.shutil.which", side_effect=_which):
        with patch("lib.thumbnail.asyncio.create_subprocess_exec") as spawn:
            result = await thumbnail_module.extract_video_last_frame(video, out)

    assert result is None
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_last_frame_returns_none_when_video_missing(tmp_path: Path):
    """video 不存在时直接返回 None，不检查 ffmpeg/ffprobe。"""
    nonexistent = tmp_path / "no-such-video.mp4"
    out = tmp_path / "out.png"

    with patch("lib.thumbnail.shutil.which") as which:
        result = await thumbnail_module.extract_video_last_frame(nonexistent, out)

    assert result is None
    which.assert_not_called()


@pytest.mark.asyncio
async def test_last_frame_falls_back_to_count_frames(tmp_path: Path):
    """容器 nb_frames 为 N/A 时回退到 -count_frames 路径。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"

    class _ProbeProc:
        """第一次返回 N/A，第二次返回 30。"""

        def __init__(self, payload: bytes, rc: int = 0):
            self._payload = payload
            self.returncode = rc

        async def communicate(self):
            return self._payload, b""

    class _FfmpegProc:
        returncode = 0

        async def wait(self):
            # 模拟 ffmpeg 写出文件
            out.write_bytes(b"\x89PNG\r\n\x1a\n")
            return None

    call_log: list[list[str]] = []
    procs = [
        _ProbeProc(b"N/A\n"),  # nb_frames 快路径
        _ProbeProc(b"30\n"),  # -count_frames 回退
        _FfmpegProc(),  # 提取
    ]

    async def _spawn(*args, **_kwargs):
        call_log.append(list(args))
        return procs.pop(0)

    def _which(name: str):
        return f"/usr/bin/{name}"

    with patch("lib.thumbnail.shutil.which", side_effect=_which):
        with patch("lib.thumbnail.asyncio.create_subprocess_exec", side_effect=_spawn):
            result = await thumbnail_module.extract_video_last_frame(video, out)

    assert result == out
    assert len(call_log) == 3
    assert call_log[0][0] == "ffprobe" and "-count_frames" not in call_log[0]
    assert call_log[1][0] == "ffprobe" and "-count_frames" in call_log[1]
    assert call_log[2][0] == "ffmpeg"


def test_ffprobe_available_is_cached():
    """_ffprobe_available() 同样走 @functools.cache。"""
    thumbnail_module._reset_for_tests()
    with patch("lib.thumbnail.shutil.which", return_value=None) as which:
        thumbnail_module._ffprobe_available()
        thumbnail_module._ffprobe_available()
        thumbnail_module._ffprobe_available()
    assert which.call_count == 1
