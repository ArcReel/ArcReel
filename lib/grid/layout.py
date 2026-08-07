"""Grid layout calculator for grid-image-to-video feature."""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd

# Base resolution for grid rendering (width reference for 16:9)
_BASE_WIDTH = 1920


@dataclass(frozen=True)
class GridLayout:
    """Describes the layout of a grid composed of multiple scene images."""

    grid_size: str
    rows: int
    cols: int
    grid_aspect_ratio: str
    cell_count: int
    placeholder_count: int

    def pixel_dimensions(self) -> tuple[int, int]:
        """Return (width, height) in pixels based on grid_aspect_ratio."""
        w_str, h_str = self.grid_aspect_ratio.split(":")
        w_ratio = int(w_str)
        h_ratio = int(h_str)
        # Scale so that the larger dimension matches the base reference
        if w_ratio >= h_ratio:
            width = _BASE_WIDTH
            height = round(_BASE_WIDTH * h_ratio / w_ratio)
        else:
            height = _BASE_WIDTH
            width = round(_BASE_WIDTH * w_ratio / h_ratio)
        return width, height


# 未配置分辨率档时宫格渲染实际下发的档位（与 ``execute_grid_task`` 同源）
GRID_FALLBACK_RESOLUTION = "2K"

# 大宫格（4×4 / 5×5）要求的图像分辨率档：单格分辨率随格数缩水，低于该档时单格已不足以
# 支撑下游视频画质，故按档位封顶到 3×3。
_LARGE_GRID_RESOLUTION = "4K"

# 档位阶梯：全部为 N×N 平方切分，单格比例因而恒等于整图比例，切格后 center-crop 近乎 no-op。
# 顺序即选档顺序，(cell_count, grid_size, side)。
_GRID_LADDER: tuple[tuple[int, str, int], ...] = (
    (4, "grid_4", 2),
    (9, "grid_9", 3),
    (16, "grid_16", 4),
    (25, "grid_25", 5),
)

# 整图比例直接取项目视频比例的规范朝向，单格比例与之一致
_ORIENTATION_ASPECT: dict[str, str] = {"horizontal": "16:9", "vertical": "9:16"}

_GATED_MAX_CELL_COUNT = 9
_MAX_CELL_COUNT = 25


def large_grid_allowed(image_resolution: str | None) -> bool:
    """4×4 / 5×5 是否可用：仅当宫格实际生效的图像分辨率档为 4K。

    ``None`` 表示调用时不传 SDK resolution 参数（见 ``docs/adr/0019``），按保底档处理。
    """
    return (image_resolution or GRID_FALLBACK_RESOLUTION).strip().upper() == _LARGE_GRID_RESOLUTION


def max_cell_count(*, allow_large_grid: bool) -> int:
    """单张宫格的格数上限；分组超出时由调用方按此切块。"""
    return _MAX_CELL_COUNT if allow_large_grid else _GATED_MAX_CELL_COUNT


def _orientation_of(aspect_ratio: str) -> str:
    """Determine orientation by comparing width and height numerically."""
    parts = aspect_ratio.split(":")
    w_ratio, h_ratio = int(parts[0]), int(parts[1])
    return "horizontal" if w_ratio > h_ratio else "vertical"


def grid_aspect_ratio_for(rows: int, cols: int, aspect_ratio: str) -> str:
    """按记录自身的 rows/cols 反推整图比例，使单格比例恒等于项目视频比例。

    方形档（rows == cols）下结果即视频比例本身，与 :func:`calculate_grid_layout` 一致；存量
    非方形记录（3×2 的 grid_6）也按同一条规则求值，重生成时整图比例与该记录的实际几何相符，
    不必为已删除的档位保留一张历史比例表。
    """
    cell_w, cell_h = (int(x) for x in _ORIENTATION_ASPECT[_orientation_of(aspect_ratio)].split(":"))
    gw, gh = cell_w * cols, cell_h * rows
    divisor = gcd(gw, gh)
    return f"{gw // divisor}:{gh // divisor}"


def calculate_grid_layout(num_scenes: int, aspect_ratio: str, *, allow_large_grid: bool = False) -> GridLayout | None:
    """Calculate the appropriate grid layout for the given number of scenes.

    Args:
        num_scenes: Number of scenes to display in the grid.
        aspect_ratio: Aspect ratio string (e.g. "16:9", "9:16", "4:3").
        allow_large_grid: 是否放行 4×4 / 5×5 档（由调用方按生效分辨率档经
            :func:`large_grid_allowed` 判定）。默认关闭，未显式传入的调用方封顶 3×3。

    Returns:
        GridLayout if num_scenes >= 1, otherwise None.
    """
    if num_scenes < 1:
        return None

    cap = max_cell_count(allow_large_grid=allow_large_grid)
    effective = min(num_scenes, cap)
    cell_count, grid_size, side = next(cfg for cfg in _GRID_LADDER if effective <= cfg[0])

    rows = cols = side
    grid_aspect_ratio = _ORIENTATION_ASPECT[_orientation_of(aspect_ratio)]
    placeholder_count = cell_count - effective

    return GridLayout(
        grid_size=grid_size,
        rows=rows,
        cols=cols,
        grid_aspect_ratio=grid_aspect_ratio,
        cell_count=cell_count,
        placeholder_count=placeholder_count,
    )
