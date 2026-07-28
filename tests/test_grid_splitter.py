"""Tests for lib/grid/splitter.py."""

from PIL import Image

from lib.grid.splitter import center_crop_to_ratio, is_placeholder_cell, split_grid_image


class TestCenterCropToRatio:
    def test_no_crop_needed(self):
        img = Image.new("RGB", (160, 90))
        result = center_crop_to_ratio(img, "16:9")
        assert result.size == (160, 90)

    def test_crop_2_1_to_16_9(self):
        img = Image.new("RGB", (200, 100))  # 2:1 → 16:9
        result = center_crop_to_ratio(img, "16:9")
        expected_w = int(100 * 16 / 9)
        assert result.size == (expected_w, 100)

    def test_crop_1_2_to_9_16(self):
        img = Image.new("RGB", (100, 200))
        result = center_crop_to_ratio(img, "9:16")
        expected_h = int(100 * 16 / 9)
        assert result.size == (100, expected_h)


class TestSplitGridImage:
    def test_split_2x2(self):
        grid = Image.new("RGB", (200, 200), color=(100, 150, 200))
        cells = split_grid_image(grid, 2, 2, "16:9", edge_margin=0.0)
        assert len(cells) == 4
        for cell in cells:
            assert abs(cell.size[0] / cell.size[1] - 16 / 9) < 0.05

    def test_split_with_margin(self):
        grid = Image.new("RGB", (400, 400), color=(128, 128, 128))
        cells = split_grid_image(grid, 2, 2, "16:9", edge_margin=0.02)
        for cell in cells:
            assert cell.size[0] < 200


class TestPlaceholderDetection:
    def test_black_is_placeholder(self):
        assert is_placeholder_cell(Image.new("RGB", (100, 100), (10, 10, 10)))

    def test_gray_is_placeholder(self):
        assert is_placeholder_cell(Image.new("RGB", (100, 100), (128, 128, 128)))

    def test_colorful_is_not(self):
        assert not is_placeholder_cell(Image.new("RGB", (100, 100), (200, 100, 50)))
