import pytest

from lib.cost_calculator import CostCalculator, cost_calculator


class TestCostCalculator:
    def test_calculate_image_cost_known_and_default(self):
        calculator = CostCalculator()
        # 默认模型 (gemini-3.1-flash-image-preview)
        assert calculator.calculate_image_cost("1k") == 0.067
        assert calculator.calculate_image_cost("2K") == 0.101
        assert calculator.calculate_image_cost("4K") == 0.151
        assert calculator.calculate_image_cost("unknown") == 0.067
        # 指定旧模型 (gemini-3-pro-image-preview)
        assert calculator.calculate_image_cost("1k", model="gemini-3-pro-image-preview") == 0.134
        assert calculator.calculate_image_cost("2K", model="gemini-3-pro-image-preview") == 0.134

    def test_calculate_video_cost_known_and_default(self):
        calculator = CostCalculator()
        # 默认模型 (veo-3.1-fast-generate-preview)
        assert calculator.calculate_video_cost(8, "1080p", True) == pytest.approx(1.2)
        assert calculator.calculate_video_cost(8, "1080p", False) == pytest.approx(0.8)
        assert calculator.calculate_video_cost(6, "4k", True) == pytest.approx(2.1)
        assert calculator.calculate_video_cost(6, "4k", False) == pytest.approx(1.8)
        assert calculator.calculate_video_cost(5, "unknown", True) == pytest.approx(0.75)
        # Fast 模型 (veo-3.1-fast-generate-001)
        fast = "veo-3.1-fast-generate-001"
        assert calculator.calculate_video_cost(8, "1080p", True, model=fast) == pytest.approx(1.2)
        assert calculator.calculate_video_cost(8, "1080p", False, model=fast) == pytest.approx(0.8)
        assert calculator.calculate_video_cost(6, "4k", True, model=fast) == pytest.approx(2.1)
        assert calculator.calculate_video_cost(6, "4k", False, model=fast) == pytest.approx(1.8)
        # Fast 模型未知分辨率应回退到自身的 1080p+audio 费率 (0.15)，而非标准模型的 0.40
        assert calculator.calculate_video_cost(5, "unknown", True, model=fast) == pytest.approx(0.75)
        # 历史兼容：preview 模型费率与 001 相同
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
