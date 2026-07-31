"""音频时长探测（lib/audio_utils.py）的降级与探测行为。"""

from __future__ import annotations

import wave
from io import BytesIO
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
