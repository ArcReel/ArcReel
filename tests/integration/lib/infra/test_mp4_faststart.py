"""Bundled ffmpeg actually remuxes a video with trailing moov into faststart."""

from __future__ import annotations

from pathlib import Path

from lib.infra.media_probe import MediaProbe, probe_media
from lib.infra.mp4_faststart import Mp4Layout, ensure_faststart, read_mp4_layout
from tests.factories import make_test_video_with_audio_tail, run_bundled_ffmpeg


def _entries(directory: Path) -> list[Path]:
    return sorted(directory.iterdir())


def _stream_summary(probe: MediaProbe) -> list[tuple[str, str | None, int]]:
    return [(stream.kind, stream.codec, stream.packet_count) for stream in probe.streams]


async def test_moov_last_download_part_is_remuxed_in_place(tmp_path: Path):
    """Downloaded ``.part`` lacks an extension: preserve audio/video streams unchanged and move moov before mdat."""
    source = tmp_path / "clip.mp4"
    make_test_video_with_audio_tail(source)  # The mp4 muxer writes moov at the end by default.
    partial = source.rename(tmp_path / "clip.mp4.part")
    assert read_mp4_layout(partial) == Mp4Layout(major_brand=b"isom", moov_first=False)
    before = await probe_media(partial)

    assert await ensure_faststart(partial) is True

    layout = read_mp4_layout(partial)
    assert layout is not None
    assert layout.moov_first
    assert _stream_summary(await probe_media(partial)) == _stream_summary(before)
    assert _entries(tmp_path) == [partial]


async def test_quicktime_stays_quicktime(tmp_path: Path):
    video = tmp_path / "clip.mov"
    run_bundled_ffmpeg(
        "-f", "lavfi", "-i", "color=black:size=64x64:duration=0.5:rate=10", "-c:v", "libx264", str(video)
    )
    assert read_mp4_layout(video) == Mp4Layout(major_brand=b"qt  ", moov_first=False)

    assert await ensure_faststart(video) is True

    assert read_mp4_layout(video) == Mp4Layout(major_brand=b"qt  ", moov_first=True)


async def test_already_faststart_video_is_untouched(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    run_bundled_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=black:size=64x64:duration=0.5:rate=10",
        "-c:v",
        "libx264",
        "-movflags",
        "+faststart",
        str(video),
    )
    original = video.read_bytes()

    assert await ensure_faststart(video) is False

    assert video.read_bytes() == original
