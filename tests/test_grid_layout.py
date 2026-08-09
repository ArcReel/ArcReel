"""Tests for grid layout calculator."""

import pytest

from lib.grid.layout import (
    GRID_FALLBACK_RESOLUTION,
    calculate_grid_layout,
    grid_aspect_ratio_for,
    large_grid_allowed,
    max_cell_count,
    plan_grid_chunks,
)
from lib.grid.models import GridGeneration, build_frame_chain
from lib.grid.prompt_builder import _compute_panel_aspect

pytestmark = pytest.mark.unit


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

    @pytest.mark.parametrize("n", [5, 6])
    def test_5_and_6_scenes_fall_into_grid_9_with_placeholders(self, n: int):
        layout = calculate_grid_layout(n, "9:16")
        assert layout is not None
        assert layout.grid_size == "grid_9"
        assert layout.rows == 3
        assert layout.cols == 3
        assert layout.grid_aspect_ratio == "9:16"
        assert layout.cell_count == 9
        assert layout.placeholder_count == 9 - n

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

    def test_below_4_uses_grid_4_with_placeholders(self):
        for n in (1, 2, 3):
            layout = calculate_grid_layout(n, "16:9")
            assert layout is not None
            assert layout.grid_size == "grid_4"
            assert layout.cell_count == 4
            assert layout.placeholder_count == 4 - n

    def test_zero_returns_none(self):
        assert calculate_grid_layout(0, "16:9") is None

    def test_above_9_caps_at_grid_9_without_large_grid(self):
        layout = calculate_grid_layout(12, "16:9")
        assert layout is not None
        assert layout.grid_size == "grid_9"
        assert layout.cell_count == 9

    @pytest.mark.parametrize(
        ("n", "grid_size", "side"),
        [
            (4, "grid_4", 2),
            (9, "grid_9", 3),
            (10, "grid_16", 4),
            (16, "grid_16", 4),
            (17, "grid_25", 5),
            (25, "grid_25", 5),
        ],
    )
    def test_large_grid_ladder(self, n: int, grid_size: str, side: int):
        layout = calculate_grid_layout(n, "16:9", allow_large_grid=True)
        assert layout is not None
        assert layout.grid_size == grid_size
        assert (layout.rows, layout.cols) == (side, side)
        assert layout.cell_count == side * side
        assert layout.placeholder_count == side * side - n

    def test_above_25_caps_at_grid_25(self):
        layout = calculate_grid_layout(40, "16:9", allow_large_grid=True)
        assert layout is not None
        assert layout.grid_size == "grid_25"
        assert layout.cell_count == 25
        assert layout.placeholder_count == 0

    @pytest.mark.parametrize("n", [10, 17, 40])
    def test_large_grid_gated_off_never_exceeds_grid_9(self, n: int):
        layout = calculate_grid_layout(n, "16:9", allow_large_grid=False)
        assert layout is not None
        assert layout.grid_size == "grid_9"
        assert layout.cell_count == 9

    @pytest.mark.parametrize("aspect_ratio", ["16:9", "9:16"])
    @pytest.mark.parametrize("n", [4, 9, 16, 25])
    def test_square_grid_aspect_equals_video_aspect(self, n: int, aspect_ratio: str):
        layout = calculate_grid_layout(n, aspect_ratio, allow_large_grid=True)
        assert layout is not None
        assert layout.rows == layout.cols
        assert layout.grid_aspect_ratio == aspect_ratio
        # 单格比例与整图一致，切格后 center-crop 是 no-op
        assert _compute_panel_aspect(layout.grid_aspect_ratio, layout.rows, layout.cols) == aspect_ratio

    def test_non_canonical_aspect_maps_to_orientation(self):
        layout = calculate_grid_layout(4, "4:3")
        assert layout is not None
        assert layout.grid_aspect_ratio == "16:9"


class TestPlanGridChunks:
    def test_empty_group_returns_no_plans(self):
        assert plan_grid_chunks([], "16:9") == []

    def test_group_within_cap_is_single_chunk(self):
        scenes = [f"S{i}" for i in range(1, 8)]
        plans = plan_grid_chunks(scenes, "16:9")
        assert len(plans) == 1
        chunk, layout = plans[0]
        assert chunk == scenes
        assert layout.grid_size == "grid_9"
        assert layout.placeholder_count == 2

    def test_group_above_cap_splits_into_multiple_chunks(self):
        scenes = [f"S{i:02d}" for i in range(1, 13)]
        plans = plan_grid_chunks(scenes, "9:16", allow_large_grid=False)
        assert [(len(chunk), layout.grid_size) for chunk, layout in plans] == [(9, "grid_9"), (3, "grid_4")]
        # 各块不重叠、并集等于整组、顺序保持
        assert [s for chunk, _ in plans for s in chunk] == scenes

    def test_remainder_chunk_falls_to_smaller_tier_with_placeholders(self):
        scenes = [f"S{i:02d}" for i in range(1, 13)]
        plans = plan_grid_chunks(scenes, "9:16")
        _, tail_layout = plans[-1]
        assert (tail_layout.rows, tail_layout.cols) == (2, 2)
        assert tail_layout.placeholder_count == 1

    def test_large_grid_keeps_group_in_one_chunk(self):
        scenes = [f"S{i:02d}" for i in range(1, 13)]
        plans = plan_grid_chunks(scenes, "16:9", allow_large_grid=True)
        assert [(len(chunk), layout.grid_size) for chunk, layout in plans] == [(12, "grid_16")]

    def test_above_25_splits_even_with_large_grid(self):
        scenes = [f"S{i:02d}" for i in range(1, 31)]
        plans = plan_grid_chunks(scenes, "16:9", allow_large_grid=True)
        assert [(len(chunk), layout.grid_size) for chunk, layout in plans] == [(25, "grid_25"), (5, "grid_9")]
        assert [s for chunk, _ in plans for s in chunk] == scenes

    def test_every_chunk_fits_its_layout(self):
        for n in (1, 4, 9, 10, 18, 27):
            plans = plan_grid_chunks(list(range(n)), "16:9", allow_large_grid=False)
            assert sum(len(chunk) for chunk, _ in plans) == n
            for chunk, layout in plans:
                assert len(chunk) <= layout.cell_count == layout.rows * layout.cols


class TestLargeGridGate:
    @pytest.mark.parametrize("resolution", ["4K", "4k", " 4K "])
    def test_4k_allows_large_grid(self, resolution: str):
        assert large_grid_allowed(resolution) is True

    @pytest.mark.parametrize("resolution", ["2K", "1K", "1080p", "", None])
    def test_below_4k_and_unset_block_large_grid(self, resolution: str | None):
        assert large_grid_allowed(resolution) is False

    def test_fallback_resolution_is_not_4k(self):
        # 未配置分辨率时保底档即渲染实际下发的档位，门控须按它判定
        assert large_grid_allowed(GRID_FALLBACK_RESOLUTION) is False

    def test_max_cell_count(self):
        assert max_cell_count(allow_large_grid=True) == 25
        assert max_cell_count(allow_large_grid=False) == 9


class TestGridAspectRatioFor:
    @pytest.mark.parametrize("side", [2, 3, 4, 5])
    def test_square_records_keep_video_aspect(self, side: int):
        assert grid_aspect_ratio_for(side, side, "16:9") == "16:9"
        assert grid_aspect_ratio_for(side, side, "9:16") == "9:16"

    def test_legacy_non_square_record_keeps_original_aspect(self):
        # 存量 grid_6 记录沿用写入时的整图比例，与其冻结的 prompt 描述的画布一致
        assert grid_aspect_ratio_for(3, 2, "16:9") == "4:3"
        assert grid_aspect_ratio_for(2, 3, "9:16") == "3:4"

    def test_unregistered_non_square_falls_back_to_video_aspect(self):
        # 未登记的非方形几何回落到 backend 尺寸表都认得的视频比例，而非自行推算的冷门比例
        assert grid_aspect_ratio_for(2, 4, "16:9") == "16:9"


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


class TestBuildFrameChain:
    def test_4_scenes_grid_4(self):
        chain = build_frame_chain(["E1S01", "E1S02", "E1S03", "E1S04"], rows=2, cols=2)
        assert len(chain) == 4
        assert chain[0].frame_type == "first"
        assert chain[0].next_scene_id == "E1S01"
        assert chain[1].frame_type == "transition"
        assert chain[1].prev_scene_id == "E1S01"
        assert chain[1].next_scene_id == "E1S02"
        assert chain[3].frame_type == "transition"
        assert chain[3].prev_scene_id == "E1S03"
        assert chain[3].next_scene_id == "E1S04"

    def test_remaining_cells_become_placeholders(self):
        chain = build_frame_chain(["S1", "S2", "S3", "S4", "S5"], rows=3, cols=3)
        assert len(chain) == 9
        assert [c.frame_type for c in chain[5:]] == ["placeholder"] * 4

    def test_row_col_assignment(self):
        chain = build_frame_chain(["A", "B", "C", "D"], rows=2, cols=2)
        assert (chain[0].row, chain[0].col) == (0, 0)
        assert (chain[1].row, chain[1].col) == (0, 1)
        assert (chain[2].row, chain[2].col) == (1, 0)
        assert (chain[3].row, chain[3].col) == (1, 1)


class TestGridGeneration:
    def test_create(self):
        grid = GridGeneration.create(
            episode=1,
            script_file="ep1.json",
            scene_ids=["E1S01", "E1S02", "E1S03", "E1S04"],
            rows=2,
            cols=2,
            grid_size="grid_4",
            provider="test",
            model="test-m",
            video_aspect_ratio="9:16",
        )
        assert grid.status == "pending"
        assert grid.cell_count == 4
        assert len(grid.frame_chain) == 4
        assert grid.id.startswith("grid_")
