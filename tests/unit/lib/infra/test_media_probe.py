"""媒体探测的纯解析函数：ffmpeg 输入信息 banner、像素格式与实测帧率（不启动子进程）。"""

from __future__ import annotations

from fractions import Fraction

from lib.infra.media_probe import (
    MediaStream,
    _bit_depth,
    _build_probe,
    _color_transfer,
    _is_variable_fps,
    _measured_fps,
    _parse_banner,
    _StreamAccumulator,
)

BANNER = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'file:clip.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:00:01.00, start: 0.000000, bitrate: 942 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 640x360 [SAR 1:1 DAR 16:9], 792 kb/s, 30 fps, 30 tbr, 15360 tbn (default)
      Metadata:
        handler_name    : VideoHandler
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, mono, fltp, 127 kb/s (default)
"""


def _banner() -> tuple:
    return _parse_banner(BANNER)


def test_banner_extracts_stream_geometry_nominal_rate_and_audio_params():
    video, audio = _banner()

    assert (video.width, video.height) == (640, 360)
    assert video.tbr == 30.0
    assert video.bit_depth == 8
    assert video.color_transfer is None
    assert video.rotation is None
    assert (audio.sample_rate, audio.channel_count) == (44100, 1)


def test_banner_reads_10_bit_transfer_curve_and_display_rotation():
    banner = BANNER.replace("yuv420p(progressive)", "yuv420p10le(tv, bt2020nc/smpte2084/bt2020, progressive)").replace(
        "handler_name    : VideoHandler",
        "Side data:\n        displaymatrix: rotation of -90.00 degrees detected",
    )
    video, audio = _parse_banner(banner)

    assert video.bit_depth == 10
    assert video.color_transfer == "smpte2084"
    assert video.rotation == -90.0
    assert audio.rotation is None


def test_banner_reads_multichannel_layout():
    video, audio = _parse_banner(BANNER.replace("44100 Hz, mono, fltp", "48000 Hz, 5.1, fltp"))

    assert (audio.sample_rate, audio.channel_count) == (48000, 6)
    assert video.width == 640


def test_banner_without_stream_lines_parses_to_nothing():
    assert _parse_banner("Input #0, mov, from 'file:x.mp4':\n  Duration: 00:00:01.00\n") == ()


def test_bit_depth_is_derived_from_the_pixel_format():
    assert _bit_depth("yuv420p") == 8
    assert _bit_depth("yuv420p10le") == 10
    assert _bit_depth("yuv422p12be") == 12
    assert _bit_depth("gbrp") == 8
    assert _bit_depth("rgb24") is None


def test_color_transfer_reads_the_middle_of_the_triple():
    assert _color_transfer("tv, bt709/unknown/unknown, progressive") is None
    assert _color_transfer("tv, bt2020nc/smpte2084/bt2020, progressive") == "smpte2084"
    assert _color_transfer("tv, bt709/bt709/bt709, progressive") == "bt709"
    assert _color_transfer("smpte2084") is None
    assert _color_transfer("progressive") is None
    assert _color_transfer(None) is None


def test_hdr_transfer_curve_marks_the_stream():
    hdr = MediaStream(kind="video", codec="h264", duration_seconds=1.0, packet_count=30, color_transfer="smpte2084")
    sdr = MediaStream(kind="video", codec="h264", duration_seconds=1.0, packet_count=30, color_transfer="bt709")

    assert hdr.is_hdr
    assert not sdr.is_hdr


def _accumulator(kind: str, time_base: Fraction | None, packet_count: int, start: int, end: int) -> _StreamAccumulator:
    accumulator = _StreamAccumulator()
    accumulator.time_base = time_base
    accumulator.kind = kind
    accumulator.start = start
    accumulator.end = end
    accumulator.packet_count = packet_count
    return accumulator


def test_measured_fps_counts_packets_over_the_covered_span():
    accumulator = _accumulator("video", Fraction(1, 15360), 30, 0, 15360)

    assert _measured_fps(accumulator, accumulator.span_seconds()) == 30.0


def test_measured_fps_is_none_without_packets_or_span():
    one_packet = _accumulator("video", Fraction(1, 30), 1, 0, 30)
    no_time_base = _accumulator("video", None, 30, 0, 30)

    assert _measured_fps(one_packet, one_packet.span_seconds()) is None
    assert _measured_fps(no_time_base, no_time_base.span_seconds()) is None


def test_variable_frame_rate_flags_large_deviation_from_the_nominal_rate():
    assert not _is_variable_fps(30.0, 30.0)
    assert not _is_variable_fps(30.2, 30.0)
    assert _is_variable_fps(31.6, 30.0)
    assert _is_variable_fps(11.25, 30.0)
    assert not _is_variable_fps(30.0, None)
    assert not _is_variable_fps(None, 30.0)


def test_build_probe_attaches_banner_info_by_stream_kind():
    banner = _parse_banner(BANNER)
    accumulators = {
        0: _accumulator("video", Fraction(1, 15360), 30, 0, 15360),
        1: _accumulator("audio", Fraction(1, 44100), 88200, 0, 44100),
    }

    probe = _build_probe(frozenset({"mov", "mp4"}), accumulators, banner)

    video = probe.first_stream("video")
    audio = probe.first_stream("audio")
    assert (video.width, video.height) == (640, 360)
    assert video.fps == 30.0
    assert not video.variable_frame_rate
    assert audio.sample_rate == 44100
    assert audio.channel_count == 1
    assert audio.fps is None
