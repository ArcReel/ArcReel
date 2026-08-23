"""lib.speech_rate 语速估算单一真相源测试。

只测公开行为（按语言取速率、按口径估时长、缺省回退），数值断言取自单一真相源
helper / 常量而非写死，避免与可调常量耦合。
"""

import pytest

from lib.speech_rate import (
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
        # 5 个阅读单位 ÷ 覆盖语速；覆盖生效即时长随之变化
        assert estimate_spoken_seconds("一二三四五", "zh", 2.0) == pytest.approx(2.5)

    def test_boundary_values(self):
        # 钉住区间字面量：断言全从常量推导时，常量被误改成 0.00095 / 20.0005 也照样通过
        assert MIN_SPEECH_RATE_UPS == 0.001
        assert MAX_SPEECH_RATE_UPS == 20.0
        assert is_valid_speech_rate(MIN_SPEECH_RATE_UPS)
        assert is_valid_speech_rate(MAX_SPEECH_RATE_UPS)
        assert not is_valid_speech_rate(0.0)
        assert not is_valid_speech_rate(0.0009)
        assert not is_valid_speech_rate(MAX_SPEECH_RATE_UPS + 0.001)

    def test_rate_below_the_lower_bound_is_out_of_range(self):
        # 下界之下的语速会让估算时长逼近双精度上限，下游微秒换算随之溢出；在入口一律拒掉，
        # 不逐个下游乘数追补有限性检查
        assert not is_valid_speech_rate(1e-302)
        assert not is_valid_speech_rate(1e-308)
        assert not is_valid_speech_rate(5e-324)
        # 被拒的覆盖按无覆盖处理，多个阅读单位照常按语言默认语速估算
        assert estimate_spoken_seconds("你好世界", "zh", 1e-308) == pytest.approx(0.8)

    def test_lower_bound_keeps_downstream_microseconds_within_int64(self):
        # 下界的取值依据：探针长度的假想文本按下界估算，换算成微秒仍远在 int64 上限内
        probe_units = 1e6
        seconds = probe_units / MIN_SPEECH_RATE_UPS
        assert int(seconds * 1_000_000) < 2**63 - 1

    def test_bool_is_not_a_rate(self):
        # bool 是 int 子类，float(True) 落在合法区间内；谓词的入参域含 project.json 解析值，故自行拒
        assert not is_valid_speech_rate(True)
        assert not is_valid_speech_rate(False)

    def test_integer_beyond_float_range_is_out_of_range(self):
        # JSON 整数字面量无位宽上限，超出双精度表示范围的整数按越界收掉而非抛 OverflowError
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
        # bool 是 int 子类，True/False 不得被当成 1.0 / 0.0 的语速
        assert project_speech_rate_override({SPEECH_RATE_FIELD: dirty}) is None

    @pytest.mark.parametrize("bad", [0, -3, MAX_SPEECH_RATE_UPS + 1, 10**400, float("inf"), float("nan")])
    def test_out_of_range_value_is_none(self, bad):
        # 手改 project.json 可写进超出双精度范围的整数：按脏值回退语言默认，不让生成崩在这里
        assert project_speech_rate_override({SPEECH_RATE_FIELD: bad}) is None
