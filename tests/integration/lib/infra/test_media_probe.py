"""媒体探测接口：对随包 ffmpeg 现场合成的素材断言容器格式、流与时长。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.infra.media_probe import MediaProbeError, probe_media
from tests.factories import make_test_video_with_audio_tail, run_bundled_ffmpeg


def _sine(path: Path, duration: float, *codec_args: str) -> Path:
    run_bundled_ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", *codec_args, str(path))
    return path


async def test_video_with_longer_audio_tail_reports_each_stream(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    make_test_video_with_audio_tail(path, video_duration_sec=1.0, audio_duration_sec=1.5, fps=30)

    probe = await probe_media(path)

    assert {"mov", "mp4"} <= probe.container_formats
    assert [(stream.kind, stream.codec) for stream in probe.streams] == [("video", "h264"), ("audio", "aac")]
    video = probe.first_stream("video")
    audio = probe.first_stream("audio")
    assert video is not None
    assert audio is not None
    assert video.duration_seconds == pytest.approx(1.0, abs=1e-6)
    assert video.packet_count == 30
    assert audio.duration_seconds == pytest.approx(1.5, abs=1e-6)
    assert probe.duration_seconds == pytest.approx(1.5, abs=1e-6)


@pytest.mark.parametrize(
    ("name", "codec_args", "container", "codec", "tolerance"),
    [
        ("tone.wav", ("-c:a", "pcm_s16le"), "wav", "pcm_s16le", 1e-6),
        ("tone.m4a", ("-c:a", "aac"), "m4a", "aac", 1e-6),
        # mp3 末尾的编码器填充只在解码时裁掉，逐包时长最多多出一帧（1152 采样）。
        ("tone.mp3", ("-c:a", "libmp3lame"), "mp3", "mp3", 1152 / 44100),
    ],
)
async def test_audio_file_reports_container_codec_and_duration(
    tmp_path: Path, name: str, codec_args: tuple[str, ...], container: str, codec: str, tolerance: float
):
    path = _sine(tmp_path / name, 3.0, *codec_args)

    probe = await probe_media(path)

    assert container in probe.container_formats
    assert [(stream.kind, stream.codec) for stream in probe.streams] == [("audio", codec)]
    assert probe.first_stream("video") is None
    assert probe.duration_seconds is not None
    assert 3.0 <= probe.duration_seconds <= 3.0 + tolerance


async def test_unparseable_file_raises(tmp_path: Path):
    path = tmp_path / "broken.wav"
    path.write_bytes(b"not audio at all")

    with pytest.raises(MediaProbeError):
        await probe_media(path)


async def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(MediaProbeError):
        await probe_media(tmp_path / "missing.mp4")


async def test_input_is_restricted_to_local_files(tmp_path: Path):
    """媒体可能嵌套 HLS/RTMP 等播放列表引用；探测必须把输入协议限定为 file，防 SSRF。"""
    path = _sine(tmp_path / "tone.wav", 1.0, "-c:a", "pcm_s16le")
    calls: list[tuple[object, ...]] = []
    orig_exec = asyncio.create_subprocess_exec

    async def _spy(*args, **kwargs):
        calls.append(args)
        return await orig_exec(*args, **kwargs)

    with patch("lib.infra.subprocess_deadline.asyncio.create_subprocess_exec", side_effect=_spy):
        probe = await probe_media(path)

    assert probe.duration_seconds == pytest.approx(1.0, abs=1e-6)
    [call_args] = calls
    assert call_args[call_args.index("-protocol_whitelist") + 1] == "file"


async def test_silent_vfr_reports_presentation_packet_span(tmp_path: Path):
    path = tmp_path / "vfr.mp4"
    run_bundled_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=64x64:rate=30:duration=2",
        "-vf",
        r"select=if(lt(t\,1)\,not(mod(n\,2))\,not(mod(n\,5)))",
        "-fps_mode",
        "vfr",
        "-c:v",
        "libx264",
        str(path),
    )

    probe = await probe_media(path)

    assert [(stream.kind, stream.codec) for stream in probe.streams] == [("video", "h264")]
    assert probe.first_stream("audio") is None
    assert probe.streams[0].packet_count == 21
    assert probe.streams[0].duration_seconds == pytest.approx(28 / 15, abs=1e-6)
    assert probe.duration_seconds == pytest.approx(28 / 15, abs=1e-6)
