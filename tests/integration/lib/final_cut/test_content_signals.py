"""成片静默段检测：现场合成的音轨里，中间与结尾的静默都检得出，无音轨返回空。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.final_cut.content_signals import detect_silence_spans
from lib.infra.ffmpeg import ffmpeg_executable
from tests.factories import make_test_clip, wav_bytes


def _concat_wav(path: Path, *wavs: bytes) -> None:
    """把若干段 8 kHz 单声道 wav 拼成一条音轨（同参数，concat 解复用器直接拼接）。"""
    directory = path.parent / "segments"
    directory.mkdir(parents=True, exist_ok=True)
    list_file = directory / "segments.txt"
    for index, data in enumerate(wavs):
        segment = directory / f"{index}.wav"
        segment.write_bytes(data)
    list_file.write_text(
        "".join(f"file '{segment.name}'\n" for segment in list(directory.glob("*.wav"))), encoding="utf-8"
    )
    subprocess.run(
        [
            ffmpeg_executable(),
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


async def test_silence_in_the_middle_is_detected(tmp_path: Path) -> None:
    audio = tmp_path / "audio.mp4"
    _concat_wav(audio, wav_bytes(2.0, tone_hz=440), wav_bytes(4.0), wav_bytes(2.0, tone_hz=440))

    spans = await detect_silence_spans(audio, duration_seconds=8.0)

    assert len(spans) == 1
    assert spans[0].start == pytest.approx(2.0, abs=0.15)
    assert spans[0].end == pytest.approx(6.0, abs=0.15)


async def test_silence_running_to_the_end_is_detected(tmp_path: Path) -> None:
    audio = tmp_path / "audio.mp4"
    _concat_wav(audio, wav_bytes(2.0, tone_hz=440), wav_bytes(4.0))

    spans = await detect_silence_spans(audio, duration_seconds=6.0)

    assert len(spans) == 1
    assert spans[0].start == pytest.approx(2.0, abs=0.15)
    assert spans[0].end == pytest.approx(6.0, abs=0.1)


async def test_continuous_tone_has_no_silence(tmp_path: Path) -> None:
    audio = tmp_path / "audio.mp4"
    _concat_wav(audio, wav_bytes(4.0, tone_hz=440))

    assert await detect_silence_spans(audio, duration_seconds=4.0) == ()


async def test_video_without_an_audio_track_yields_no_spans(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    make_test_clip(video, size="64x64", fps=24, seconds=2.0, tone=False)

    assert await detect_silence_spans(video, duration_seconds=2.0) == ()
