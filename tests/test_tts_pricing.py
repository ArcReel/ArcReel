"""TTS 按字符计费：PerCharacter 策略 + lookup 钉死 audio 计费类型 + cost_calculator 内置分支。"""

from __future__ import annotations

import pytest

from lib.cost_calculator import cost_calculator
from lib.pricing.lookup import lookup_pricing
from lib.pricing.strategies import PricingParams, calculate_pricing
from lib.pricing.types import PerCharacter


class TestPerCharacterStrategy:
    pricing = PerCharacter(rates={"qwen3-tts-flash": 0.8}, default_model="qwen3-tts-flash", currency="CNY")

    def test_known_model(self):
        amount, currency = calculate_pricing(
            self.pricing, PricingParams(call_type="audio", model="qwen3-tts-flash", usage_tokens=10_000)
        )
        assert currency == "CNY"
        assert amount == pytest.approx(0.8)

    def test_partial_chars(self):
        amount, _ = calculate_pricing(
            self.pricing, PricingParams(call_type="audio", model="qwen3-tts-flash", usage_tokens=1500)
        )
        assert amount == pytest.approx(1500 / 10_000 * 0.8)

    def test_zero_chars(self):
        amount, _ = calculate_pricing(
            self.pricing, PricingParams(call_type="audio", model="qwen3-tts-flash", usage_tokens=0)
        )
        assert amount == pytest.approx(0.0)

    def test_unknown_model_falls_back_to_default(self):
        amount, _ = calculate_pricing(
            self.pricing, PricingParams(call_type="audio", model="unknown-tts", usage_tokens=10_000)
        )
        assert amount == pytest.approx(0.8)

    def test_none_usage_tokens_is_zero(self):
        amount, _ = calculate_pricing(self.pricing, PricingParams(call_type="audio", model="qwen3-tts-flash"))
        assert amount == pytest.approx(0.0)


class TestAudioLookupPinsPerCharacter:
    """钉死 audio 计费类型：DashScope audio 已知/未知 model 都必须返回 PerCharacter。

    防未来加第二个 audio provider 却漏设 default=True audio 模型 → 回落 gemini text PerToken
    → audio 只设字符数(usage_tokens) → _per_token 读 input/output_tokens 算出 0 → 静默计零。
    """

    def test_known_model_is_per_character(self):
        pricing = lookup_pricing("dashscope", "qwen3-tts-flash", "audio")
        assert isinstance(pricing, PerCharacter)
        assert pricing.rates["qwen3-tts-flash"] == 0.8
        assert pricing.currency == "CNY"

    def test_unknown_model_still_per_character(self):
        pricing = lookup_pricing("dashscope", "qwen3-tts-pro-not-registered", "audio")
        assert isinstance(pricing, PerCharacter)
        assert pricing.default_model == "qwen3-tts-flash"


class TestBuiltinAudioCost:
    def test_dashscope_audio_non_zero_cny(self):
        amount, currency = cost_calculator.calculate_cost(
            provider="dashscope", call_type="audio", model="qwen3-tts-flash", usage_tokens=1500
        )
        assert currency == "CNY"
        assert amount == pytest.approx(0.12)

    def test_zero_chars_zero_cost(self):
        amount, currency = cost_calculator.calculate_cost(
            provider="dashscope", call_type="audio", model="qwen3-tts-flash", usage_tokens=0
        )
        assert currency == "CNY"
        assert amount == pytest.approx(0.0)


# 自定义供应商 audio 计费（按用户填写的每万字符单价）覆盖在 tests/test_custom_cost.py，
# 与 text/image/video 的自定义计费测试同址；本文件只覆盖内置 per_character 路径。
