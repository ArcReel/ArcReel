import pytest

from lib.cost_calculator import CostCalculator, cost_calculator


class TestCostCalculator:
    def test_calculate_image_cost_known_and_default(self):
        calculator = CostCalculator()
        # Default model (gemini-3.1-flash-image-preview)
        assert calculator.calculate_image_cost("1k") == 0.067
        assert calculator.calculate_image_cost("2K") == 0.101
        assert calculator.calculate_image_cost("4K") == 0.151
        assert calculator.calculate_image_cost("unknown") == 0.067
        # Older model (gemini-3-pro-image-preview)
        assert calculator.calculate_image_cost("1k", model="gemini-3-pro-image-preview") == 0.134
        assert calculator.calculate_image_cost("2K", model="gemini-3-pro-image-preview") == 0.134

    def test_calculate_video_cost_known_and_default(self):
        calculator = CostCalculator()
        # Default model (veo-3.1-lite-generate-preview)
        assert calculator.calculate_video_cost(8, "1080p", True) == pytest.approx(0.64)
        assert calculator.calculate_video_cost(8, "1080p", False) == pytest.approx(0.64)
        assert calculator.calculate_video_cost(8, "720p", True) == pytest.approx(0.40)
        assert calculator.calculate_video_cost(8, "720p", False) == pytest.approx(0.40)
        # Lite does not support 4K, unknown resolution falls back to 1080p+audio rate (0.08)
        assert calculator.calculate_video_cost(5, "unknown", True) == pytest.approx(0.40)
        # Fast model (veo-3.1-fast-generate-001)
        fast = "veo-3.1-fast-generate-001"
        assert calculator.calculate_video_cost(8, "1080p", True, model=fast) == pytest.approx(1.2)
        assert calculator.calculate_video_cost(8, "1080p", False, model=fast) == pytest.approx(0.8)
        assert calculator.calculate_video_cost(6, "4k", True, model=fast) == pytest.approx(2.1)
        assert calculator.calculate_video_cost(6, "4k", False, model=fast) == pytest.approx(1.8)
        # Fast model unknown resolution falls back to its own 1080p+audio rate (0.15), not the standard model's 0.40
        assert calculator.calculate_video_cost(5, "unknown", True, model=fast) == pytest.approx(0.75)
        # Legacy compatibility: preview model rates same as 001
        preview = "veo-3.1-generate-preview"
        assert calculator.calculate_video_cost(8, "1080p", True, model=preview) == pytest.approx(3.2)
        assert calculator.calculate_video_cost(8, "1080p", False, model=preview) == pytest.approx(1.6)
        fast_preview = "veo-3.1-fast-generate-preview"
        assert calculator.calculate_video_cost(8, "1080p", True, model=fast_preview) == pytest.approx(1.2)

    def test_singleton_instance(self):
        assert isinstance(cost_calculator, CostCalculator)


class TestArkCost:
    def test_online_with_audio(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_ark_video_cost(
            usage_tokens=246840,
            service_tier="default",
            generate_audio=True,
            model="doubao-seedance-1-5-pro-251215",
        )
        assert currency == "CNY"
        assert amount == pytest.approx(3.9494, rel=1e-3)

    def test_online_no_audio(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_ark_video_cost(
            usage_tokens=246840,
            service_tier="default",
            generate_audio=False,
        )
        assert currency == "CNY"
        assert amount == pytest.approx(1.9747, rel=1e-3)

    def test_flex_with_audio(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_ark_video_cost(
            usage_tokens=246840,
            service_tier="flex",
            generate_audio=True,
        )
        assert currency == "CNY"
        assert amount == pytest.approx(1.9747, rel=1e-3)

    def test_flex_no_audio(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_ark_video_cost(
            usage_tokens=246840,
            service_tier="flex",
            generate_audio=False,
        )
        assert currency == "CNY"
        assert amount == pytest.approx(0.9874, rel=1e-3)

    def test_zero_tokens(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_ark_video_cost(
            usage_tokens=0,
            service_tier="default",
            generate_audio=True,
        )
        assert amount == pytest.approx(0.0)
        assert currency == "CNY"

    def test_unknown_model_uses_default(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_ark_video_cost(
            usage_tokens=1_000_000,
            service_tier="default",
            generate_audio=True,
            model="unknown-model",
        )
        assert currency == "CNY"
        assert amount == pytest.approx(16.0)

    def test_seedance_2_cost(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_ark_video_cost(
            usage_tokens=1_000_000,
            service_tier="default",
            generate_audio=True,
            model="doubao-seedance-2-0-260128",
        )
        assert currency == "CNY"
        assert amount == pytest.approx(46.00)

    def test_seedance_2_cost_no_audio_same_price(self):
        calculator = CostCalculator()
        amount, _ = calculator.calculate_ark_video_cost(
            usage_tokens=1_000_000,
            service_tier="default",
            generate_audio=False,
            model="doubao-seedance-2-0-260128",
        )
        assert amount == pytest.approx(46.00)

    def test_seedance_2_fast_cost(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_ark_video_cost(
            usage_tokens=1_000_000,
            service_tier="default",
            generate_audio=True,
            model="doubao-seedance-2-0-fast-260128",
        )
        assert currency == "CNY"
        assert amount == pytest.approx(37.00)


class TestGrokCost:
    def test_default_model_per_second(self):
        calculator = CostCalculator()
        cost, currency = calculator.calculate_grok_video_cost(
            duration_seconds=10,
            model="grok-imagine-video",
        )
        assert cost == pytest.approx(0.50)
        assert currency == "USD"

    def test_short_video(self):
        calculator = CostCalculator()
        cost, currency = calculator.calculate_grok_video_cost(
            duration_seconds=1,
            model="grok-imagine-video",
        )
        assert cost == pytest.approx(0.050)
        assert currency == "USD"

    def test_max_duration(self):
        calculator = CostCalculator()
        cost, _ = calculator.calculate_grok_video_cost(
            duration_seconds=15,
            model="grok-imagine-video",
        )
        assert cost == pytest.approx(0.75)

    def test_zero_duration(self):
        calculator = CostCalculator()
        cost, _ = calculator.calculate_grok_video_cost(
            duration_seconds=0,
            model="grok-imagine-video",
        )
        assert cost == pytest.approx(0.0)

    def test_unknown_model_uses_default(self):
        calculator = CostCalculator()
        cost, _ = calculator.calculate_grok_video_cost(
            duration_seconds=10,
            model="unknown-grok-model",
        )
        assert cost == pytest.approx(0.50)


class TestArkImageCost:
    def test_ark_image_cost_default(self):
        cost, currency = cost_calculator.calculate_ark_image_cost()
        assert currency == "CNY"
        assert cost == pytest.approx(0.22)

    def test_ark_image_cost_by_model(self):
        cost, _ = cost_calculator.calculate_ark_image_cost(model="doubao-seedream-4-5-251128")
        assert cost == pytest.approx(0.25)

    def test_ark_image_cost_n_images(self):
        cost, _ = cost_calculator.calculate_ark_image_cost(n=3)
        assert cost == pytest.approx(0.22 * 3)

    def test_ark_image_cost_unknown_model(self):
        cost, currency = cost_calculator.calculate_ark_image_cost(model="unknown-model")
        assert currency == "CNY"
        assert cost == pytest.approx(0.22)


class TestGrokImageCost:
    def test_grok_image_cost_default(self):
        cost, currency = cost_calculator.calculate_grok_image_cost()
        assert cost == pytest.approx(0.02)
        assert currency == "USD"

    def test_grok_image_cost_pro(self):
        cost, currency = cost_calculator.calculate_grok_image_cost(model="grok-imagine-image-pro")
        assert cost == pytest.approx(0.07)
        assert currency == "USD"

    def test_grok_image_cost_n_images(self):
        cost, _ = cost_calculator.calculate_grok_image_cost(n=4)
        assert cost == pytest.approx(0.02 * 4)

    def test_grok_image_cost_unknown_model(self):
        cost, currency = cost_calculator.calculate_grok_image_cost(model="unknown-model")
        assert cost == pytest.approx(0.02)
        assert currency == "USD"


class TestOpenAICost:
    def test_openai_text_cost(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_text_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            provider="openai",
            model="gpt-5.4-mini",
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.75 + 4.50)

    def test_openai_text_cost_default_model(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_text_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            provider="openai",
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.75)

    def test_openai_image_cost_square(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_openai_image_cost(model="gpt-image-1.5", quality="medium")
        assert currency == "USD"
        assert amount == pytest.approx(0.034)  # Default 1024x1024

    def test_openai_image_cost_portrait(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_openai_image_cost(
            model="gpt-image-1.5",
            quality="medium",
            size="1024x1792",
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.051)

    def test_openai_image_cost_landscape(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_openai_image_cost(
            model="gpt-image-1.5",
            quality="high",
            size="1792x1024",
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.200)

    def test_openai_image_cost_low(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_openai_image_cost(model="gpt-image-1-mini", quality="low")
        assert currency == "USD"
        assert amount == pytest.approx(0.005)

    def test_openai_image_cost_mini_portrait(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_openai_image_cost(
            model="gpt-image-1-mini",
            quality="medium",
            size="1024x1792",
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.017)

    def test_openai_video_cost(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_openai_video_cost(duration_seconds=8, model="sora-2")
        assert currency == "USD"
        assert amount == pytest.approx(0.80)

    def test_openai_video_cost_pro(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_openai_video_cost(
            duration_seconds=4, model="sora-2-pro", resolution="1080p"
        )
        assert currency == "USD"
        assert amount == pytest.approx(2.80)

    def test_unified_entry_openai(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_cost("openai", "text", input_tokens=500_000, output_tokens=100_000)
        assert amount == pytest.approx(0.375 + 0.45)
        amount, currency = calculator.calculate_cost("openai", "image", model="gpt-image-1.5", quality="high")
        assert amount == pytest.approx(0.133)  # Default 1024x1024
        amount, currency = calculator.calculate_cost(
            "openai", "image", model="gpt-image-1.5", quality="high", size="1024x1792"
        )
        assert amount == pytest.approx(0.200)
        amount, currency = calculator.calculate_cost("openai", "video", duration_seconds=12, model="sora-2")
        assert amount == pytest.approx(1.20)
