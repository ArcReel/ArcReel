"""媒体时长探测（lib/audio_utils.py）的降级与探测行为。"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import lib.audio_utils as audio_utils_module
from tests.factories import make_test_video_with_audio_tail, wav_bytes


@pytest.fixture(autouse=True)
def _reset_ffprobe_cache():
    audio_utils_module._reset_for_tests()
    yield
    audio_utils_module._reset_for_tests()


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


def _m4a_bytes(duration_seconds: float = 3.0) -> bytes:
    """生成一段有音轨但容器不是 wav/mp3 的 m4a（供"容器改名不改内容"用例复现）。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = Path(tmp_dir) / "audio.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={duration_seconds}",
                "-c:a",
                "aac",
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )
        return out_path.read_bytes()


class TestFfprobeUnavailable:
    async def test_returns_none_without_spawning(self):
        with (
            patch("lib.audio_utils.shutil.which", return_value=None),
            patch("lib.audio_utils.asyncio.create_subprocess_exec") as spawn,
        ):
            result = await audio_utils_module.probe_audio_duration_seconds(wav_bytes(3), ".wav")
        assert result is None
        spawn.assert_not_called()


class TestFfprobeAvailable:
    @pytest.fixture(autouse=True)
    def check_ffprobe(self):
        import shutil

        if shutil.which("ffprobe") is None:
            pytest.skip("ffprobe not available")

    async def test_probes_real_duration(self):
        duration = await audio_utils_module.probe_audio_duration_seconds(wav_bytes(3), ".wav")
        assert duration is not None
        assert 2.5 < duration < 3.5

    async def test_invalid_bytes_raise_value_error(self):
        with pytest.raises(ValueError, match=r"音频文件无法解析"):
            await audio_utils_module.probe_audio_duration_seconds(b"not audio at all", ".wav")

    async def test_video_only_file_renamed_to_wav_is_rejected(self):
        """把无音轨的视频文件改名为 .wav 上传时，容器/时长校验会通过，但应无音频流可用而拒绝。"""
        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg not available")
        with pytest.raises(ValueError, match=r"音频文件无法解析"):
            await audio_utils_module.probe_audio_duration_seconds(_video_only_mp4_bytes(), ".wav")

    async def test_m4a_renamed_to_wav_is_rejected(self):
        """m4a 有音轨也能探出时长，但容器不是 wav，改名上传应被拒绝而非当作 wav 收下。"""
        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg not available")
        with pytest.raises(ValueError, match=r"音频文件无法解析"):
            await audio_utils_module.probe_audio_duration_seconds(_m4a_bytes(), ".wav")

    async def test_ffprobe_invoked_with_protocol_whitelist(self):
        """探测字节可能嵌套 HLS/RTMP 等播放列表引用；每次 ffprobe 调用都必须限制协议白名单为 file，防 SSRF。"""
        calls: list[tuple[object, ...]] = []
        orig_exec = asyncio.create_subprocess_exec

        async def _spy(*args, **kwargs):
            calls.append(args)
            return await orig_exec(*args, **kwargs)

        with patch("lib.audio_utils.asyncio.create_subprocess_exec", side_effect=_spy):
            await audio_utils_module.probe_audio_duration_seconds(wav_bytes(3), ".wav")

        assert calls, "ffprobe 应至少被调用一次"
        for call_args in calls:
            assert "-protocol_whitelist" in call_args
            assert call_args[call_args.index("-protocol_whitelist") + 1] == "file"


class TestProbeExistingVideoDuration:
    @pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="ffmpeg/ffprobe not available",
    )
    async def test_real_video_uses_track_boundary_instead_of_audio_tail(self, tmp_path):
        path = tmp_path / "clip.mp4"
        make_test_video_with_audio_tail(path)

        video_duration = await audio_utils_module.probe_existing_video_duration_seconds(path)
        container_duration = await audio_utils_module.probe_existing_media_duration_seconds(path)

        assert video_duration == pytest.approx(1.0, abs=0.01)
        assert container_duration == pytest.approx(1.5, abs=0.01)

    async def test_prefers_video_stream_duration_over_longer_container_tail(self, tmp_path):
        probe = AsyncMock(return_value=b'{"streams":[{"duration":"1.000000"}],"format":{"duration":"1.500000"}}')
        duration = await audio_utils_module.probe_existing_video_duration_seconds(
            tmp_path / "clip.mp4",
            ffprobe_available=lambda: True,
            run_ffprobe=probe,
        )

        assert duration == 1.0

    async def test_falls_back_to_container_duration_when_stream_duration_is_absent(self, tmp_path):
        probe = AsyncMock(return_value=b'{"streams":[{}],"format":{"duration":"1.500000"}}')
        duration = await audio_utils_module.probe_existing_video_duration_seconds(
            tmp_path / "clip.mp4",
            ffprobe_available=lambda: True,
            run_ffprobe=probe,
        )

        assert duration == 1.5

    async def test_rejects_container_without_a_video_stream(self, tmp_path):
        probe = AsyncMock(return_value=b'{"streams":[],"format":{"duration":"1.500000"}}')
        duration = await audio_utils_module.probe_existing_video_duration_seconds(
            tmp_path / "clip.mp4",
            ffprobe_available=lambda: True,
            run_ffprobe=probe,
        )

        assert duration is None


class TestProbeReferenceAudioTotalSeconds:
    @pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not available")
    async def test_sums_durations_of_existing_files(self, tmp_path):
        path_a = tmp_path / "a.wav"
        path_b = tmp_path / "b.wav"
        path_a.write_bytes(wav_bytes(3))
        path_b.write_bytes(wav_bytes(5))

        total = await audio_utils_module.probe_reference_audio_total_seconds([path_a, path_b])

        assert total is not None
        assert 7.5 < total < 8.5

    async def test_empty_list_returns_zero(self):
        total = await audio_utils_module.probe_reference_audio_total_seconds([])
        assert total == 0.0

    @pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not available")
    async def test_unreadable_file_returns_none_not_partial_sum(self, tmp_path):
        """半截总时长比跳过校验更危险：任一文件探测失败就整体判 None，不能只算成功的部分。"""
        path_a = tmp_path / "a.wav"
        path_missing = tmp_path / "missing.wav"
        path_a.write_bytes(wav_bytes(3))

        total = await audio_utils_module.probe_reference_audio_total_seconds([path_a, path_missing])

        assert total is None

    async def test_ffprobe_unavailable_returns_none(self, tmp_path):
        path_a = tmp_path / "a.wav"
        path_a.write_bytes(wav_bytes(3))
        with patch("lib.audio_utils.shutil.which", return_value=None):
            total = await audio_utils_module.probe_reference_audio_total_seconds([path_a])
        assert total is None
