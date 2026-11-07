"""Tests for lib.speech.speech_rate as the single speech-rate source.

Test only public behavior (language rates, duration estimates, default fallback), deriving numeric
assertions from shared helpers/constants instead of hardcoding tunable values.
"""

import pytest

from lib.speech.speech_rate import (
    DEFAULT_SPEECH_RATE_UPS,
    MAX_SPEECH_RATE_UPS,
    MIN_SPEECH_RATE_UPS,
    SPEECH_RATE_FIELD,
    estimate_spoken_seconds,
    is_valid_speech_rate,
    project_speech_rate_override,
    speech_rate_units_per_second,
)


class TestSpeechRateUnitsPerSecond:
    def test_none_and_empty_fall_back_to_default(self):
        assert speech_rate_units_per_second(None) == DEFAULT_SPEECH_RATE_UPS
        assert speech_rate_units_per_second("") == DEFAULT_SPEECH_RATE_UPS

    def test_unregistered_language_falls_back_to_default(self):
        assert speech_rate_units_per_second("klingon") == DEFAULT_SPEECH_RATE_UPS

    def test_registered_languages_return_positive_rate(self):
        assert speech_rate_units_per_second("zh") > 0
        assert speech_rate_units_per_second("en") > 0
        assert speech_rate_units_per_second("vi") > 0
        assert speech_rate_units_per_second("ru") > 0

    def test_ru_shares_latin_word_rate(self):
        # ru counts words, using the en / vi rate (2.5 words/second).
        assert speech_rate_units_per_second("ru") == speech_rate_units_per_second("en")
        assert speech_rate_units_per_second("ru") == speech_rate_units_per_second("vi")

    def test_language_code_is_case_insensitive(self):
        assert speech_rate_units_per_second("EN") == speech_rate_units_per_second("en")


class TestEstimateSpokenSeconds:
    def test_empty_or_whitespace_is_zero(self):
        assert estimate_spoken_seconds("", "zh") == 0.0
        assert estimate_spoken_seconds("   ", "zh") == 0.0

    def test_none_text_is_zero(self):
        assert estimate_spoken_seconds(None, "zh") == 0.0

    def test_duration_is_reading_units_over_rate(self):
        # 5 Chinese reading units ÷ zh rate; derive units from the shared helper rather than hardcoding seconds.
        expected = 5 / speech_rate_units_per_second("zh")
        assert estimate_spoken_seconds("一二三四五", "zh") == pytest.approx(expected)

    def test_longer_text_takes_longer(self):
        short = estimate_spoken_seconds("一二", "zh")
        longer = estimate_spoken_seconds("一二三四五六七八", "zh")
        assert longer > short

    def test_language_changes_timing(self):
        # The same 5 reading units use zh character rates versus en word rates:
        # language controls both reading-unit counting and rate; neither may fall back to defaults.
        zh = estimate_spoken_seconds("一二三四五", "zh")
        en = estimate_spoken_seconds("one two three four five", "en")
        assert zh == pytest.approx(5 / speech_rate_units_per_second("zh"))
        assert en == pytest.approx(5 / speech_rate_units_per_second("en"))
        assert zh != en

    def test_unknown_language_uses_default_rate(self):
        expected = 5 / DEFAULT_SPEECH_RATE_UPS
        assert estimate_spoken_seconds("一二三四五", None) == pytest.approx(expected)


class TestSpeechRateOverride:
    def test_override_wins_over_language_default(self):
        assert speech_rate_units_per_second("zh", 3.0) == 3.0
        assert speech_rate_units_per_second("en", 3.0) == 3.0

    def test_none_override_falls_back_to_language_default(self):
        assert speech_rate_units_per_second("en", None) == speech_rate_units_per_second("en")

    def test_out_of_range_override_falls_back_to_language_default(self):
        for bad in (0.0, -1.0, MAX_SPEECH_RATE_UPS + 0.1, float("inf"), float("nan")):
            assert speech_rate_units_per_second("zh", bad) == speech_rate_units_per_second("zh")

    def test_estimate_uses_override(self):
        # 5 reading units ÷ override rate; an effective override changes duration.
        assert estimate_spoken_seconds("一二三四五", "zh", 2.0) == pytest.approx(2.5)

    def test_boundary_values(self):
        # Lock literal bounds: constant-derived assertions would also pass erroneous 0.00095 / 20.0005 values.
        assert MIN_SPEECH_RATE_UPS == 0.001
        assert MAX_SPEECH_RATE_UPS == 20.0
        assert is_valid_speech_rate(MIN_SPEECH_RATE_UPS)
        assert is_valid_speech_rate(MAX_SPEECH_RATE_UPS)
        assert not is_valid_speech_rate(0.0)
        assert not is_valid_speech_rate(0.0009)
        assert not is_valid_speech_rate(MAX_SPEECH_RATE_UPS + 0.001)

    def test_rate_below_the_lower_bound_is_out_of_range(self):
        # Rates below the lower bound approach double limits and overflow downstream microseconds; reject at entry,
        # rather than adding finiteness checks separately for each downstream multiplier.
        assert not is_valid_speech_rate(1e-302)
        assert not is_valid_speech_rate(1e-308)
        assert not is_valid_speech_rate(5e-324)
        # Rejected overrides act as absent; estimate multiple units at the language-default rate.
        assert estimate_spoken_seconds("你好世界", "zh", 1e-308) == pytest.approx(0.8)

    def test_lower_bound_keeps_downstream_microseconds_within_int64(self):
        # Lower-bound rationale: hypothetical probe-length text still converts to microseconds far within int64.
        probe_units = 1e6
        seconds = probe_units / MIN_SPEECH_RATE_UPS
        assert int(seconds * 1_000_000) < 2**63 - 1

    def test_bool_is_not_a_rate(self):
        # bool subclasses int and float(True) falls inside bounds; inputs include parsed project.json, so reject explicitly.
        assert not is_valid_speech_rate(True)
        assert not is_valid_speech_rate(False)

    def test_integer_beyond_float_range_is_out_of_range(self):
        # JSON integers are unbounded; values beyond double representation count as out-of-range, not OverflowError.
        assert not is_valid_speech_rate(10**400)
        assert not is_valid_speech_rate(-(10**400))


class TestProjectSpeechRateOverride:
    def test_missing_field_is_none(self):
        assert project_speech_rate_override({}) is None
        assert project_speech_rate_override(None) is None

    def test_valid_value_is_returned(self):
        assert project_speech_rate_override({SPEECH_RATE_FIELD: 6}) == 6.0
        assert project_speech_rate_override({SPEECH_RATE_FIELD: 6.5}) == 6.5

    @pytest.mark.parametrize("dirty", ["6", None, [], {}, True, False])
    def test_dirty_value_is_none(self, dirty):
        # bool subclasses int; True/False must not become 1.0 / 0.0 speech rates.
        assert project_speech_rate_override({SPEECH_RATE_FIELD: dirty}) is None

    @pytest.mark.parametrize("bad", [0, -3, MAX_SPEECH_RATE_UPS + 1, 10**400, float("inf"), float("nan")])
    def test_out_of_range_value_is_none(self, bad):
        # Hand-edited project.json may contain integers beyond doubles; fall back to language defaults instead of crashing generation.
        assert project_speech_rate_override({SPEECH_RATE_FIELD: bad}) is None
