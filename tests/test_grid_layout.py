"""Tests for grid layout calculator."""

from lib.grid.layout import calculate_grid_layout


class TestCalculateGridLayout:
    def test_4_scenes_horizontal(self):
        layout = calculate_grid_layout(4, "16:9")
        assert layout is not None
        assert layout.grid_size == "grid_4"
        assert layout.rows == 2
        assert layout.cols == 2
        assert layout.grid_aspect_ratio == "16:9"
        assert layout.cell_count == 4
        assert layout.placeholder_count == 0

    def test_4_scenes_vertical(self):
        layout = calculate_grid_layout(4, "9:16")
        assert layout is not None
        assert layout.grid_size == "grid_4"
        assert layout.rows == 2
        assert layout.cols == 2
        assert layout.grid_aspect_ratio == "9:16"
        assert layout.cell_count == 4
        assert layout.placeholder_count == 0

    def test_5_scenes_uses_grid_6(self):
        layout = calculate_grid_layout(5, "4:3")
        assert layout is not None
        assert layout.grid_size == "grid_6"
        assert layout.rows == 3
        assert layout.cols == 2
        assert layout.grid_aspect_ratio == "4:3"
        assert layout.cell_count == 6
        assert layout.placeholder_count == 1

    def test_5_scenes_vertical_uses_grid_6(self):
        layout = calculate_grid_layout(5, "9:16")
        assert layout is not None
        assert layout.grid_size == "grid_6"
        assert layout.rows == 2
        assert layout.cols == 3
        assert layout.grid_aspect_ratio == "3:4"
        assert layout.cell_count == 6
        assert layout.placeholder_count == 1

    def test_6_scenes(self):
        layout = calculate_grid_layout(6, "4:3")
        assert layout is not None
        assert layout.grid_size == "grid_6"
        assert layout.cell_count == 6
        assert layout.placeholder_count == 0

    def test_7_scenes_uses_grid_9(self):
        layout = calculate_grid_layout(7, "16:9")
        assert layout is not None
        assert layout.grid_size == "grid_9"
        assert layout.rows == 3
        assert layout.cols == 3
        assert layout.cell_count == 9
        assert layout.placeholder_count == 2

    def test_9_scenes(self):
        layout = calculate_grid_layout(9, "16:9")
        assert layout is not None
        assert layout.grid_size == "grid_9"
        assert layout.cell_count == 9
        assert layout.placeholder_count == 0

    def test_below_4_returns_none(self):
        assert calculate_grid_layout(1, "16:9") is None
        assert calculate_grid_layout(2, "16:9") is None
        assert calculate_grid_layout(3, "16:9") is None

    def test_above_9_caps_at_grid_9(self):
        layout = calculate_grid_layout(12, "16:9")
        assert layout is not None
        assert layout.grid_size == "grid_9"
        assert layout.cell_count == 9


class TestGridLayoutPixelDimensions:
    def test_16_9_pixel_dimensions(self):
        layout = calculate_grid_layout(4, "16:9")
        assert layout is not None
        width, height = layout.pixel_dimensions()
        assert width > 0
        assert height > 0
        # 16:9 ratio
        assert abs(width / height - 16 / 9) < 0.01

    def test_9_16_pixel_dimensions(self):
        layout = calculate_grid_layout(4, "9:16")
        assert layout is not None
        width, height = layout.pixel_dimensions()
        assert width > 0
        assert height > 0
        # 9:16 ratio
        assert abs(width / height - 9 / 16) < 0.01
