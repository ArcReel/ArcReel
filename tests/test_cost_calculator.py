import pytest

from lib.cost_calculator import CostCalculator, cost_calculator


class TestCostCalculator:
    def test_calculate_image_cost_known_and_default(self):
        calculator = CostCalculator()
        assert calculator.calculate_image_cost("1k") == 0.134
        assert calculator.calculate_image_cost("2K") == 0.134
        assert calculator.calculate_image_cost("4K") == 0.24
        assert calculator.calculate_image_cost("unknown") == 0.134

    def test_calculate_video_cost_known_and_default(self):
        calculator = CostCalculator()
        assert calculator.calculate_video_cost(8, "1080p", True) == pytest.approx(3.2)
        assert calculator.calculate_video_cost(8, "1080p", False) == pytest.approx(1.6)
        assert calculator.calculate_video_cost(6, "4k", True) == pytest.approx(3.6)
        assert calculator.calculate_video_cost(6, "4k", False) == pytest.approx(2.4)
        assert calculator.calculate_video_cost(5, "unknown", True) == pytest.approx(2.0)

    def test_singleton_instance(self):
        assert isinstance(cost_calculator, CostCalculator)
