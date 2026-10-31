"""成片响度归一：目标常量、归一滤镜链选择与验收警告的判定。"""

from __future__ import annotations

from pathlib import Path

from lib.bgm.loudness import LoudnessSummary
from lib.final_cut.ffmpeg_render import AUDIO_SAMPLE_RATE, normalize_reencode_args
from lib.final_cut.loudness import (
    INTEGRATED_WARNING_TOLERANCE_LUFS,
    TARGET_INTEGRATED_LUFS,
    TARGET_TRUE_PEAK_DBTP,
    is_silent_loudness,
    loudness_warnings,
    normalize_audio_filter,
    normalize_gain_db,
)

MEASUREMENT = LoudnessSummary(integrated_lufs=-24.8, true_peak_dbtp=-18.1, lra_lu=8.9, threshold_lufs=-34.7)


def test_linear_gain_that_stays_under_the_peak_limit_uses_two_pass_loudnorm() -> None:
    assert normalize_audio_filter(MEASUREMENT) == (
        f"loudnorm=I={TARGET_INTEGRATED_LUFS:g}:TP={TARGET_TRUE_PEAK_DBTP:g}:LRA=11"
        ":measured_I=-24.8:measured_TP=-18.1:measured_LRA=8.9:measured_thresh=-34.7:linear=true"
    )


def test_zero_lra_and_negative_seventy_threshold_are_clamped_into_the_linear_branch() -> None:
    measurement = LoudnessSummary(integrated_lufs=-24.8, true_peak_dbtp=-18.1, lra_lu=0.0, threshold_lufs=-70.0)

    assert normalize_audio_filter(measurement) == (
        f"loudnorm=I={TARGET_INTEGRATED_LUFS:g}:TP={TARGET_TRUE_PEAK_DBTP:g}:LRA=11"
        ":measured_I=-24.8:measured_TP=-18.1:measured_LRA=0.01:measured_thresh=-69.99:linear=true"
    )


def test_large_measured_lra_is_clamped_to_the_target_range() -> None:
    measurement = LoudnessSummary(integrated_lufs=-22.6, true_peak_dbtp=-20.6, lra_lu=20.0, threshold_lufs=-32.9)

    assert "measured_LRA=11" in normalize_audio_filter(measurement)


def test_gain_that_overruns_the_peak_limit_falls_back_to_volume_plus_limiter() -> None:
    peaking = LoudnessSummary(integrated_lufs=-17.0, true_peak_dbtp=-1.2, lra_lu=5.0, threshold_lufs=-30.0)
    limit = 10.0 ** (TARGET_TRUE_PEAK_DBTP / 20.0)

    assert normalize_audio_filter(peaking) == f"volume=3dB,alimiter=limit={limit:.6f}:level=false:latency=true"


def test_missing_readings_fall_back_to_volume_plus_limiter() -> None:
    limit = 10.0 ** (TARGET_TRUE_PEAK_DBTP / 20.0)
    for measurement in (
        LoudnessSummary(integrated_lufs=-24.8),
        LoudnessSummary(integrated_lufs=-24.8, true_peak_dbtp=float("-inf"), lra_lu=8.9, threshold_lufs=-34.7),
        LoudnessSummary(integrated_lufs=-24.8, true_peak_dbtp=-18.1, threshold_lufs=-34.7),
    ):
        assert (
            normalize_audio_filter(measurement) == f"volume=10.8dB,alimiter=limit={limit:.6f}:level=false:latency=true"
        )


def test_an_unmeasurable_loudness_is_limited_without_a_gain() -> None:
    limit = 10.0 ** (TARGET_TRUE_PEAK_DBTP / 20.0)
    silent = LoudnessSummary(integrated_lufs=float("-inf"))

    assert normalize_audio_filter(silent) == f"alimiter=limit={limit:.6f}:level=false:latency=true"


def test_normalize_gain_raises_quiet_mixes_to_the_target() -> None:
    assert normalize_gain_db(MEASUREMENT) == 10.8


def test_silence_is_detected_at_the_r128_absolute_threshold() -> None:
    assert is_silent_loudness(-70.0)
    assert is_silent_loudness(float("-inf"))
    assert not is_silent_loudness(-69.9)


def test_measurements_inside_the_tolerances_raise_no_warning() -> None:
    on_target = LoudnessSummary(
        integrated_lufs=TARGET_INTEGRATED_LUFS + INTEGRATED_WARNING_TOLERANCE_LUFS,
        true_peak_dbtp=TARGET_TRUE_PEAK_DBTP + 0.5,
    )

    assert loudness_warnings(on_target) == ()


def test_integrated_loudness_beyond_the_tolerance_is_warned() -> None:
    loud = LoudnessSummary(
        integrated_lufs=TARGET_INTEGRATED_LUFS + INTEGRATED_WARNING_TOLERANCE_LUFS + 0.1,
        true_peak_dbtp=TARGET_TRUE_PEAK_DBTP,
    )

    warnings = loudness_warnings(loud)

    assert len(warnings) == 1
    assert "LUFS" in warnings[0]
    assert "dBTP" not in warnings[0]


def test_true_peak_beyond_the_tolerance_is_warned() -> None:
    peaking = LoudnessSummary(
        integrated_lufs=TARGET_INTEGRATED_LUFS,
        true_peak_dbtp=TARGET_TRUE_PEAK_DBTP + 0.5 + 0.1,
    )

    warnings = loudness_warnings(peaking)

    assert len(warnings) == 1
    assert "dBTP" in warnings[0]


def test_a_silent_final_cut_gets_no_loudness_warning() -> None:
    assert loudness_warnings(LoudnessSummary(integrated_lufs=-70.0, true_peak_dbtp=float("-inf"))) == ()


def test_an_unmeasurable_final_cut_is_warned() -> None:
    assert loudness_warnings(None) == ("成片响度无法实测",)


def test_reencode_args_trim_the_mix_to_its_sample_count_and_match_the_mix_encoding() -> None:
    args = normalize_reencode_args(
        "/ffmpeg", Path("mix.m4a"), Path("normalized.m4a"), MEASUREMENT, total_samples=154368
    )

    assert args[:3] == ["/ffmpeg", "-hide_banner", "-nostdin"]
    filter_position = args.index("-af")
    assert args[filter_position + 1] == (
        "atrim=end_sample=154368,asetpts=PTS-STARTPTS,"
        f"loudnorm=I={TARGET_INTEGRATED_LUFS:g}:TP={TARGET_TRUE_PEAK_DBTP:g}:LRA=11"
        ":measured_I=-24.8:measured_TP=-18.1:measured_LRA=8.9:measured_thresh=-34.7:linear=true"
    )
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-b:a") + 1] == "192k"
    assert args[args.index("-ar") + 1] == str(AUDIO_SAMPLE_RATE)
