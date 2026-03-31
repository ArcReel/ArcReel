"""Tests for CostCalculator custom provider cost calculation."""

from __future__ import annotations

from lib.cost_calculator import CostCalculator


class TestCustomTextCost:
    """Test text cost calculation for custom providers."""

    def test_custom_text_cost(self):
        calc = CostCalculator()
        amount, currency = calc.calculate_cost(
            "custom-3",
            "text",
            model="deepseek-v3",
            input_tokens=1000,
            output_tokens=500,
            custom_price_input=1.0,
            custom_price_output=2.0,
            custom_currency="USD",
        )
        assert currency == "USD"
        # (1000 * 1.0 + 500 * 2.0) / 1_000_000 = 0.002
        assert abs(amount - 0.002) < 0.0001

    def test_custom_text_cost_cny(self):
        calc = CostCalculator()
        amount, currency = calc.calculate_cost(
            "custom-7",
            "text",
            model="qwen-turbo",
            input_tokens=2000,
            output_tokens=1000,
            custom_price_input=0.5,
            custom_price_output=1.0,
            custom_currency="CNY",
        )
        assert currency == "CNY"
        # (2000 * 0.5 + 1000 * 1.0) / 1_000_000 = 0.002
        assert abs(amount - 0.002) < 0.0001


class TestCustomImageCost:
    """Test image cost calculation for custom providers."""

    def test_custom_image_cost(self):
        calc = CostCalculator()
        amount, currency = calc.calculate_cost(
            "custom-5",
            "image",
            model="dall-e-3",
            custom_price_input=0.05,
            custom_currency="USD",
        )
        assert currency == "USD"
        assert abs(amount - 0.05) < 0.0001

    def test_custom_image_cost_cny(self):
        calc = CostCalculator()
        amount, currency = calc.calculate_cost(
            "custom-1",
            "image",
            model="seedream",
            custom_price_input=0.22,
            custom_currency="CNY",
        )
        assert currency == "CNY"
        assert abs(amount - 0.22) < 0.0001


class TestCustomVideoCost:
    """Test video cost calculation for custom providers."""

    def test_custom_video_cost(self):
        calc = CostCalculator()
        amount, currency = calc.calculate_cost(
            "custom-2",
            "video",
            model="sora-2",
            duration_seconds=10,
            custom_price_input=0.10,
            custom_currency="USD",
        )
        assert currency == "USD"
        # 10 * 0.10 = 1.0
        assert abs(amount - 1.0) < 0.0001

    def test_custom_video_cost_default_duration(self):
        calc = CostCalculator()
        amount, currency = calc.calculate_cost(
            "custom-4",
            "video",
            model="some-video-model",
            custom_price_input=0.05,
            custom_currency="USD",
        )
        assert currency == "USD"
        # default 8 seconds * 0.05 = 0.4
        assert abs(amount - 0.4) < 0.0001


class TestCustomCostNullPrice:
    """Test that null/missing price returns 0."""

    def test_null_price_returns_zero(self):
        calc = CostCalculator()
        amount, currency = calc.calculate_cost(
            "custom-1",
            "text",
            model="some-model",
            input_tokens=1000,
            output_tokens=500,
            custom_price_input=None,
            custom_price_output=None,
            custom_currency=None,
        )
        assert amount == 0.0
        assert currency == "USD"

    def test_no_custom_price_returns_zero(self):
        calc = CostCalculator()
        amount, currency = calc.calculate_cost(
            "custom-99",
            "text",
            model="nonexistent",
            input_tokens=1000,
            output_tokens=500,
        )
        assert amount == 0.0
        assert currency == "USD"

    def test_null_currency_defaults_to_usd(self):
        calc = CostCalculator()
        amount, currency = calc.calculate_cost(
            "custom-1",
            "text",
            model="model",
            input_tokens=1000,
            output_tokens=500,
            custom_price_input=1.0,
            custom_price_output=2.0,
            custom_currency=None,
        )
        assert currency == "USD"
