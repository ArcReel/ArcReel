"""BGM 响度：ebur128 汇总的解析与折算到目标响度的静态增益。"""

from __future__ import annotations

import pytest

from lib.bgm.library import TARGET_LOUDNESS_LUFS, loudness_gain_db
from lib.bgm.loudness import (
    LoudnessMeasurementError,
    parse_integrated_loudness,
    parse_loudness_summary,
)

SUMMARY = """[Parsed_ebur128_0 @ 0x0] Summary:

  Integrated loudness:
    I:         -23.4 LUFS
    Threshold: -33.6 LUFS

  Loudness range:
    LRA:         0.0 LU
"""

SUMMARY_WITH_TRUE_PEAK = (
    SUMMARY
    + """
  True peak:
    Peak:      -3.2 dBFS
"""
)


def test_integrated_loudness_is_read_from_the_summary() -> None:
    assert parse_integrated_loudness("frame log noise\n" + SUMMARY) == -23.4


def test_silence_reads_as_negative_infinity() -> None:
    assert parse_integrated_loudness(SUMMARY.replace("-23.4", "-inf")) == float("-inf")


def test_output_without_a_summary_is_a_measurement_error() -> None:
    with pytest.raises(LoudnessMeasurementError):
        parse_integrated_loudness("Invalid data found when processing input")


@pytest.mark.parametrize(("integrated", "gain_db"), [(-23.4, 7.4), (-16.0, 0.0), (-9.25, -6.75)])
def test_gain_brings_the_measured_loudness_to_the_target(integrated: float, gain_db: float) -> None:
    assert loudness_gain_db(integrated) == gain_db
    assert integrated + loudness_gain_db(integrated) == pytest.approx(TARGET_LOUDNESS_LUFS)


def test_the_summary_reports_every_section_it_contains() -> None:
    summary = parse_loudness_summary(SUMMARY_WITH_TRUE_PEAK)

    assert summary.integrated_lufs == -23.4
    assert summary.threshold_lufs == -33.6
    assert summary.lra_lu == 0.0
    assert summary.true_peak_dbtp == -3.2


def test_a_summary_without_true_peak_reports_no_peak() -> None:
    assert parse_loudness_summary(SUMMARY).true_peak_dbtp is None


def test_silent_true_peak_reads_as_negative_infinity() -> None:
    summary = parse_loudness_summary(SUMMARY_WITH_TRUE_PEAK.replace("-3.2", "-inf"))

    assert summary.true_peak_dbtp == float("-inf")
