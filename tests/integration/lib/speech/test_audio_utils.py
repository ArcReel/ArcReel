"""媒体时长探测（lib/speech/audio_utils.py）的降级与探测行为。"""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

import lib.speech.audio_utils as audio_utils_module
from lib.infra.ffmpeg import reset_for_tests
from tests.factories import make_test_video_with_audio_tail, run_bundled_ffmpeg, wav_bytes


@contextmanager
def _bundled_ffmpeg_unavailable() -> Generator[None, None, None]:
    reset_for_tests()
    with (
        tempfile.TemporaryDirectory() as binaries_dir,
        patch("lib.infra.ffmpeg.importlib.resources.files", return_value=Path(binaries_dir)),
    ):
        try:
            yield
        finally:
            reset_for_tests()


def _synthesized_bytes(name: str, *args: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = Path(tmp_dir) / name
        run_bundled_ffmpeg(*args, str(out_path))
        return out_path.read_bytes()


def _video_only_mp4_bytes(duration_seconds: float = 1.0) -> bytes:
    """生成一段无音轨的极小 MP4（供"视频改名为 .wav 上传"用例复现）。"""
    return _synthesized_bytes(
        "video.mp4",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration_seconds}:size=32x32:rate=5",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    )


def _m4a_bytes(duration_seconds: float = 3.0) -> bytes:
    """生成一段有音轨但容器不是 wav/mp3 的 m4a（供"容器改名不改内容"用例复现）。"""
    return _synthesized_bytes(
        "audio.m4a", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_seconds}", "-c:a", "aac"
    )


class TestBundledFfmpegUnavailable:
    async def test_upload_probe_returns_none_without_spawning(self):
        with (
            _bundled_ffmpeg_unavailable(),
            patch("lib.infra.subprocess_deadline.asyncio.create_subprocess_exec") as spawn,
        ):
            result = await audio_utils_module.probe_audio_duration_seconds(wav_bytes(3), ".wav")
        assert result is None
        spawn.assert_not_called()

    async def test_reference_audio_total_returns_none(self, tmp_path):
        path_a = tmp_path / "a.wav"
        path_a.write_bytes(wav_bytes(3))
        with _bundled_ffmpeg_unavailable():
            total = await audio_utils_module.probe_reference_audio_total_seconds([path_a])
        assert total is None


class TestProbeAudioDuration:
    async def test_probes_real_duration(self):
        duration = await audio_utils_module.probe_audio_duration_seconds(wav_bytes(3), ".wav")
        assert duration == pytest.approx(3.0, abs=1e-6)

    async def test_invalid_bytes_raise_value_error(self):
        with pytest.raises(ValueError, match=r"音频文件无法解析"):
            await audio_utils_module.probe_audio_duration_seconds(b"not audio at all", ".wav")

    async def test_video_only_file_renamed_to_wav_is_rejected(self):
        """把无音轨的视频文件改名为 .wav 上传时，容器/时长校验会通过，但应无音频流可用而拒绝。"""
        with pytest.raises(ValueError, match=r"音频文件无法解析"):
            await audio_utils_module.probe_audio_duration_seconds(_video_only_mp4_bytes(), ".wav")

    async def test_m4a_renamed_to_wav_is_rejected(self):
        """m4a 有音轨也能探出时长，但容器不是 wav，改名上传应被拒绝而非当作 wav 收下。"""
        with pytest.raises(ValueError, match=r"音频文件无法解析"):
            await audio_utils_module.probe_audio_duration_seconds(_m4a_bytes(), ".wav")


class TestProbeExistingVideoDuration:
    async def test_real_video_uses_track_boundary_instead_of_audio_tail(self, tmp_path):
        path = tmp_path / "clip.mp4"
        make_test_video_with_audio_tail(path)

        video_duration = await audio_utils_module.probe_existing_video_duration_seconds(path)
        container_duration = await audio_utils_module.probe_existing_media_duration_seconds(path)

        assert video_duration == pytest.approx(1.0, abs=0.01)
        assert container_duration == pytest.approx(1.5, abs=0.01)

    async def test_rejects_container_without_a_video_stream(self, tmp_path):
        path = tmp_path / "tone.wav"
        path.write_bytes(wav_bytes(3))

        assert await audio_utils_module.probe_existing_video_duration_seconds(path) is None


class TestProbeReferenceAudioTotalSeconds:
    async def test_sums_durations_of_existing_files(self, tmp_path):
        path_a = tmp_path / "a.wav"
        path_b = tmp_path / "b.wav"
        path_a.write_bytes(wav_bytes(3))
        path_b.write_bytes(wav_bytes(5))

        total = await audio_utils_module.probe_reference_audio_total_seconds([path_a, path_b])

        assert total == pytest.approx(8.0, abs=1e-6)

    async def test_empty_list_returns_zero(self):
        total = await audio_utils_module.probe_reference_audio_total_seconds([])
        assert total == 0.0

    async def test_unreadable_file_returns_none_not_partial_sum(self, tmp_path):
        """半截总时长比跳过校验更危险：任一文件探测失败就整体判 None，不能只算成功的部分。"""
        path_a = tmp_path / "a.wav"
        path_missing = tmp_path / "missing.wav"
        path_a.write_bytes(wav_bytes(3))

        total = await audio_utils_module.probe_reference_audio_total_seconds([path_a, path_missing])

        assert total is None
