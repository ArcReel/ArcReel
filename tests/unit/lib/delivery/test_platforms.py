"""平台交付规格：规格表、画幅推导默认平台与交付检查的 PASS/WARN/FAIL 判定。"""

from __future__ import annotations

from lib.delivery.platforms import (
    PLATFORMS,
    DeliveryRow,
    check_delivery,
    default_platform,
)
from lib.infra.media_probe import MediaProbe, MediaStream


def _stream(**overrides: object) -> MediaStream:
    values: dict[str, object] = {
        "kind": "video",
        "codec": "h264",
        "duration_seconds": 119.0,
        "packet_count": 3570,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
    }
    values.update(overrides)
    return MediaStream(**values)


def _probe(video: MediaStream | None, audio: MediaStream | None, formats: frozenset[str]) -> MediaProbe:
    streams: list[MediaStream] = []
    if video is not None:
        streams.append(video)
    if audio is not None:
        streams.append(audio)
    return MediaProbe(container_formats=formats, streams=tuple(streams), duration_seconds=119.0)


def _mp4_probe(**video_overrides: object) -> MediaProbe:
    audio = MediaStream(kind="audio", codec="aac", duration_seconds=119.0, packet_count=3570)
    return _probe(_stream(**video_overrides), audio, frozenset({"mov", "mp4", "m4a"}))


def _rows(check) -> dict[str, DeliveryRow]:
    return {row.field: row for row in check.rows}


def test_default_platform_follows_orientation() -> None:
    assert default_platform(1080, 1920) == "douyin"
    assert default_platform(1080, 1080) == "douyin"
    assert default_platform(1920, 1080) == "youtube"


def test_supported_platforms_cover_the_first_batch() -> None:
    assert set(PLATFORMS) == {"douyin", "tiktok", "youtube", "bilibili", "x", "instagram"}
    for spec in PLATFORMS.values():
        assert spec.name_key.startswith("delivery_platform_")


def test_clean_douyin_render_passes_every_row() -> None:
    check = check_delivery(
        _mp4_probe(),
        "douyin",
        file_size_bytes=200 * 1024**2,
        integrated_lufs=-14.0,
        true_peak_dbtp=-2.0,
    )
    assert check.ok
    assert [row.status for row in check.rows] == ["pass"] * len(check.rows)
    assert _rows(check)["aspect_ratio"].actual == "9:16"


def test_unsupported_aspect_ratio_fails_with_hint() -> None:
    check = check_delivery(_mp4_probe(width=1920, height=1080), "instagram", file_size_bytes=1)
    row = _rows(check)["aspect_ratio"]
    assert row.status == "fail"
    assert row.actual == "16:9"
    assert row.hint_key == "delivery_hint_aspect_ratio_unsupported"
    assert row.hint_params["ratio"] == "16:9"


def test_low_short_edge_fails_resolution() -> None:
    check = check_delivery(_mp4_probe(width=480, height=854), "douyin", file_size_bytes=1)
    row = _rows(check)["resolution"]
    assert row.status == "fail"
    assert row.hint_key == "delivery_hint_resolution_low"
    assert row.hint_params["short_edge"] == 480


def test_duration_over_the_hard_limit_fails() -> None:
    check = check_delivery(_mp4_probe(duration_seconds=950.0), "douyin", file_size_bytes=1)
    row = _rows(check)["duration"]
    assert (row.status, row.hint_key) == ("fail", "delivery_hint_duration_over")


def test_duration_near_the_limit_warns() -> None:
    check = check_delivery(_mp4_probe(duration_seconds=850.0), "douyin", file_size_bytes=1)
    row = _rows(check)["duration"]
    assert (row.status, row.hint_key) == ("warn", "delivery_hint_duration_near_limit")


def test_duration_over_the_free_tier_soft_limit_warns_on_x() -> None:
    check = check_delivery(_mp4_probe(duration_seconds=200.0, width=1920, height=1080), "x", file_size_bytes=1)
    row = _rows(check)["duration"]
    assert (row.status, row.hint_key) == ("warn", "delivery_hint_duration_soft_limit")


def test_duration_over_x_hard_limit_fails() -> None:
    check = check_delivery(_mp4_probe(duration_seconds=650.0), "x", file_size_bytes=1)
    row = _rows(check)["duration"]
    assert (row.status, row.hint_key) == ("fail", "delivery_hint_duration_over")


def test_vertical_duration_over_youtube_shorts_warns() -> None:
    check = check_delivery(_mp4_probe(duration_seconds=200.0), "youtube", file_size_bytes=1)
    row = _rows(check)["duration"]
    assert (row.status, row.hint_key) == ("warn", "delivery_hint_shorts_over")


def test_horizontal_duration_over_youtube_shorts_still_passes() -> None:
    check = check_delivery(_mp4_probe(duration_seconds=200.0, width=1920, height=1080), "youtube", file_size_bytes=1)
    assert _rows(check)["duration"].status == "pass"


def test_fps_out_of_range_fails() -> None:
    check = check_delivery(_mp4_probe(fps=15.0), "douyin", file_size_bytes=1)
    row = _rows(check)["fps"]
    assert (row.status, row.hint_key) == ("fail", "delivery_hint_fps_out_of_range")


def test_unsupported_container_fails() -> None:
    probe = _probe(_stream(), None, frozenset({"matroska", "webm"}))
    check = check_delivery(probe, "douyin", file_size_bytes=1)
    row = _rows(check)["container"]
    assert (row.status, row.hint_key) == ("fail", "delivery_hint_container_unsupported")
    assert "matroska" in (row.actual or "")


def test_unsupported_video_codec_fails() -> None:
    check = check_delivery(_mp4_probe(codec="hevc"), "douyin", file_size_bytes=1)
    row = _rows(check)["video_codec"]
    assert (row.status, row.hint_key) == ("fail", "delivery_hint_video_codec_unsupported")


def test_missing_audio_stream_fails_audio_codec() -> None:
    check = check_delivery(_probe(_stream(), None, frozenset({"mov", "mp4"})), "douyin", file_size_bytes=1)
    row = _rows(check)["audio_codec"]
    assert row.status == "fail"
    assert row.actual == "无音轨"


def test_missing_video_metrics_fail_aspect_ratio() -> None:
    check = check_delivery(
        _probe(_stream(width=None, height=None), None, frozenset({"mov", "mp4"})), "douyin", file_size_bytes=1
    )
    row = _rows(check)["aspect_ratio"]
    assert (row.status, row.hint_key) == ("fail", "delivery_hint_probe_incomplete")


def test_probe_without_a_video_stream_reports_incomplete_rows() -> None:
    audio = MediaStream(kind="audio", codec="aac", duration_seconds=119.0, packet_count=3570)
    check = check_delivery(_probe(None, audio, frozenset({"mp3", "ogg"})), "douyin", file_size_bytes=1)
    rows = _rows(check)

    assert rows["aspect_ratio"].status == "fail"
    assert rows["video_codec"].status == "fail"
    assert rows["container"].status == "fail"
    assert rows["audio_codec"].status == "pass"
    assert "duration" not in rows
    assert "fps" not in rows
    assert "resolution" not in rows


def test_file_size_over_the_limit_fails() -> None:
    check = check_delivery(_mp4_probe(), "douyin", file_size_bytes=5 * 1024**3)
    row = _rows(check)["file_size"]
    assert (row.status, row.hint_key) == ("fail", "delivery_hint_file_size_over")


def test_loudness_off_target_warns() -> None:
    check = check_delivery(_mp4_probe(), "douyin", file_size_bytes=1, integrated_lufs=-10.0)
    row = _rows(check)["loudness"]
    assert (row.status, row.hint_key) == ("warn", "delivery_hint_loudness_off")


def test_loudness_on_target_passes() -> None:
    check = check_delivery(_mp4_probe(), "douyin", file_size_bytes=1, integrated_lufs=-14.0)
    assert _rows(check)["loudness"].status == "pass"


def test_silent_loudness_passes_as_silent() -> None:
    check = check_delivery(_mp4_probe(), "douyin", file_size_bytes=1, integrated_lufs=-70.0)
    row = _rows(check)["loudness"]
    assert (row.status, row.actual) == ("pass", "静音")


def test_missing_loudness_skips_the_row() -> None:
    check = check_delivery(_mp4_probe(), "douyin", file_size_bytes=1)
    assert "loudness" not in _rows(check)
    assert "true_peak" not in _rows(check)


def test_true_peak_over_the_limit_warns() -> None:
    check = check_delivery(_mp4_probe(), "douyin", file_size_bytes=1, true_peak_dbtp=-0.5)
    row = _rows(check)["true_peak"]
    assert (row.status, row.hint_key) == ("warn", "delivery_hint_true_peak_high")


def test_fail_and_warn_counters() -> None:
    check = check_delivery(
        _mp4_probe(duration_seconds=950.0, codec="hevc"),
        "douyin",
        file_size_bytes=1,
        integrated_lufs=-10.0,
        true_peak_dbtp=-0.5,
    )
    assert check.fail_count == 2
    assert check.warn_count == 2
    assert not check.ok
