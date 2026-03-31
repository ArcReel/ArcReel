"""Tests for CostCalculator custom provider cost calculation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lib.cost_calculator import CostCalculator


class TestCustomTextCost:
    """Test text cost calculation for custom providers."""

    def test_custom_text_cost(self):
        calc = CostCalculator()
        mock_model = MagicMock(price_input=1.0, price_output=2.0, currency="USD")
        with patch.object(calc, "_get_custom_model_price", return_value=mock_model):
            amount, currency = calc.calculate_cost(
                "custom-3",
                "text",
                model="deepseek-v3",
                input_tokens=1000,
                output_tokens=500,
            )
        assert currency == "USD"
        # (1000 * 1.0 + 500 * 2.0) / 1_000_000 = 0.002
        assert abs(amount - 0.002) < 0.0001

    def test_custom_text_cost_cny(self):
        calc = CostCalculator()
        mock_model = MagicMock(price_input=0.5, price_output=1.0, currency="CNY")
        with patch.object(calc, "_get_custom_model_price", return_value=mock_model):
            amount, currency = calc.calculate_cost(
                "custom-7",
                "text",
                model="qwen-turbo",
                input_tokens=2000,
                output_tokens=1000,
            )
        assert currency == "CNY"
        # (2000 * 0.5 + 1000 * 1.0) / 1_000_000 = 0.002
        assert abs(amount - 0.002) < 0.0001


class TestCustomImageCost:
    """Test image cost calculation for custom providers."""

    def test_custom_image_cost(self):
        calc = CostCalculator()
        mock_model = MagicMock(price_input=0.05, price_output=None, currency="USD")
        with patch.object(calc, "_get_custom_model_price", return_value=mock_model):
            amount, currency = calc.calculate_cost(
                "custom-5",
                "image",
                model="dall-e-3",
            )
        assert currency == "USD"
        assert abs(amount - 0.05) < 0.0001

    def test_custom_image_cost_cny(self):
        calc = CostCalculator()
        mock_model = MagicMock(price_input=0.22, price_output=None, currency="CNY")
        with patch.object(calc, "_get_custom_model_price", return_value=mock_model):
            amount, currency = calc.calculate_cost(
                "custom-1",
                "image",
                model="seedream",
            )
        assert currency == "CNY"
        assert abs(amount - 0.22) < 0.0001


class TestCustomVideoCost:
    """Test video cost calculation for custom providers."""

    def test_custom_video_cost(self):
        calc = CostCalculator()
        mock_model = MagicMock(price_input=0.10, price_output=None, currency="USD")
        with patch.object(calc, "_get_custom_model_price", return_value=mock_model):
            amount, currency = calc.calculate_cost(
                "custom-2",
                "video",
                model="sora-2",
                duration_seconds=10,
            )
        assert currency == "USD"
        # 10 * 0.10 = 1.0
        assert abs(amount - 1.0) < 0.0001

    def test_custom_video_cost_default_duration(self):
        calc = CostCalculator()
        mock_model = MagicMock(price_input=0.05, price_output=None, currency="USD")
        with patch.object(calc, "_get_custom_model_price", return_value=mock_model):
            amount, currency = calc.calculate_cost(
                "custom-4",
                "video",
                model="some-video-model",
            )
        assert currency == "USD"
        # default 8 seconds * 0.05 = 0.4
        assert abs(amount - 0.4) < 0.0001


class TestCustomCostNullPrice:
    """Test that null/missing price returns 0."""

    def test_null_price_returns_zero(self):
        calc = CostCalculator()
        mock_model = MagicMock(price_input=None, price_output=None, currency=None)
        with patch.object(calc, "_get_custom_model_price", return_value=mock_model):
            amount, currency = calc.calculate_cost(
                "custom-1",
                "text",
                model="some-model",
                input_tokens=1000,
                output_tokens=500,
            )
        assert amount == 0.0
        assert currency == "USD"

    def test_model_not_found_returns_zero(self):
        calc = CostCalculator()
        with patch.object(calc, "_get_custom_model_price", return_value=None):
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
        mock_model = MagicMock(price_input=1.0, price_output=2.0, currency=None)
        with patch.object(calc, "_get_custom_model_price", return_value=mock_model):
            amount, currency = calc.calculate_cost(
                "custom-1",
                "text",
                model="model",
                input_tokens=1000,
                output_tokens=500,
            )
        assert currency == "USD"
