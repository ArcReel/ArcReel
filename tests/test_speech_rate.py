"""lib.speech_rate 语速估算单一真相源测试。

只测公开行为（按语言取速率、按口径估时长、缺省回退），数值断言取自单一真相源
helper / 常量而非写死，避免与可调常量耦合。
"""

import pytest

from lib.speech_rate import (
    DEFAULT_SPEECH_RATE_UPS,
    estimate_spoken_seconds,
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

    def test_language_code_is_case_insensitive(self):
        assert speech_rate_units_per_second("EN") == speech_rate_units_per_second("en")


class TestEstimateSpokenSeconds:
    def test_empty_or_whitespace_is_zero(self):
        assert estimate_spoken_seconds("", "zh") == 0.0
        assert estimate_spoken_seconds("   ", "zh") == 0.0

    def test_none_text_is_zero(self):
        assert estimate_spoken_seconds(None, "zh") == 0.0

    def test_duration_is_reading_units_over_rate(self):
        # 5 个汉字阅读单位 ÷ zh 语速；口径取自单一真相源 helper，不写死秒数。
        expected = 5 / speech_rate_units_per_second("zh")
        assert estimate_spoken_seconds("一二三四五", "zh") == pytest.approx(expected)

    def test_longer_text_takes_longer(self):
        short = estimate_spoken_seconds("一二", "zh")
        longer = estimate_spoken_seconds("一二三四五六七八", "zh")
        assert longer > short

    def test_language_changes_timing(self):
        # 同为 5 个阅读单位，zh（计字）与 en（计词）语速不同 → 时长不同，
        # 证明语速随语言变化、非全局写死单值。
        zh = estimate_spoken_seconds("一二三四五", "zh")
        en = estimate_spoken_seconds("one two three four five", "en")
        assert zh != en

    def test_unknown_language_uses_default_rate(self):
        expected = 5 / DEFAULT_SPEECH_RATE_UPS
        assert estimate_spoken_seconds("一二三四五", None) == pytest.approx(expected)
