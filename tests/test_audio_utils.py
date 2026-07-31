"""音频时长探测（lib/audio_utils.py）的降级与探测行为。"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import wave
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

import lib.audio_utils as audio_utils_module


@pytest.fixture(autouse=True)
def _reset_ffprobe_cache():
    audio_utils_module._reset_for_tests()
    yield
    audio_utils_module._reset_for_tests()


def _wav_bytes(duration_seconds: float, sample_rate: int = 8000) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * int(duration_seconds * sample_rate))
    return buf.getvalue()


def _video_only_mp4_bytes(duration_seconds: float = 1.0) -> bytes:
    """生成一段无音轨的极小 MP4（供"视频改名为 .wav 上传"用例复现）。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = Path(tmp_dir) / "video.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc=duration={duration_seconds}:size=32x32:rate=5",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )
        return out_path.read_bytes()


class TestFfprobeUnavailable:
    async def test_returns_none_without_spawning(self):
        with patch("lib.audio_utils.shutil.which", return_value=None):
            with patch("lib.audio_utils.asyncio.create_subprocess_exec") as spawn:
                result = await audio_utils_module.probe_audio_duration_seconds(_wav_bytes(3), ".wav")
        assert result is None
        spawn.assert_not_called()


class TestFfprobeAvailable:
    @pytest.fixture(autouse=True)
    def check_ffprobe(self):
        import shutil

        if shutil.which("ffprobe") is None:
            pytest.skip("ffprobe not available")

    async def test_probes_real_duration(self):
        duration = await audio_utils_module.probe_audio_duration_seconds(_wav_bytes(3), ".wav")
        assert duration is not None
        assert 2.5 < duration < 3.5

    async def test_invalid_bytes_raise_value_error(self):
        with pytest.raises(ValueError):
            await audio_utils_module.probe_audio_duration_seconds(b"not audio at all", ".wav")

    async def test_video_only_file_renamed_to_wav_is_rejected(self):
        """把无音轨的视频文件改名为 .wav 上传时，容器/时长校验会通过，但应无音频流可用而拒绝。"""
        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg not available")
        with pytest.raises(ValueError):
            await audio_utils_module.probe_audio_duration_seconds(_video_only_mp4_bytes(), ".wav")

    async def test_ffprobe_invoked_with_protocol_whitelist(self):
        """探测字节可能嵌套 HLS/RTMP 等播放列表引用；每次 ffprobe 调用都必须限制协议白名单为 file，防 SSRF。"""
        calls: list[tuple[object, ...]] = []
        orig_exec = asyncio.create_subprocess_exec

        async def _spy(*args, **kwargs):
            calls.append(args)
            return await orig_exec(*args, **kwargs)

        with patch("lib.audio_utils.asyncio.create_subprocess_exec", side_effect=_spy):
            await audio_utils_module.probe_audio_duration_seconds(_wav_bytes(3), ".wav")

        assert calls, "ffprobe 应至少被调用一次"
        for call_args in calls:
            assert "-protocol_whitelist" in call_args
            assert call_args[call_args.index("-protocol_whitelist") + 1] == "file"
