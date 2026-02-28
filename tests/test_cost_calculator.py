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
        # 默认模型 (veo-3.1-generate-preview)
        assert calculator.calculate_video_cost(8, "1080p", True) == pytest.approx(3.2)
        assert calculator.calculate_video_cost(8, "1080p", False) == pytest.approx(1.6)
        assert calculator.calculate_video_cost(6, "4k", True) == pytest.approx(3.6)
        assert calculator.calculate_video_cost(6, "4k", False) == pytest.approx(2.4)
        assert calculator.calculate_video_cost(5, "unknown", True) == pytest.approx(2.0)
        # Fast 模型 (veo-3.1-fast-generate-preview)
        fast = "veo-3.1-fast-generate-preview"
        assert calculator.calculate_video_cost(8, "1080p", True, model=fast) == pytest.approx(1.2)
        assert calculator.calculate_video_cost(8, "1080p", False, model=fast) == pytest.approx(0.8)
        assert calculator.calculate_video_cost(6, "4k", True, model=fast) == pytest.approx(2.1)
        assert calculator.calculate_video_cost(6, "4k", False, model=fast) == pytest.approx(1.8)
        # Fast 模型未知分辨率应回退到自身的 1080p+audio 费率 (0.15)，而非标准模型的 0.40
        assert calculator.calculate_video_cost(5, "unknown", True, model=fast) == pytest.approx(0.75)

    def test_singleton_instance(self):
        assert isinstance(cost_calculator, CostCalculator)
