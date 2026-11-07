"""Grid layout calculator for grid-image-to-video feature."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

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


# Actual grid resolution when unconfigured (shared with ``execute_grid_task``)
GRID_FALLBACK_RESOLUTION = "2K"

# Required resolution for large grids (4×4 / 5×5): cells shrink with count; below this tier individual
# cells cannot support downstream video quality, so cap at 3×3.
_LARGE_GRID_RESOLUTION = "4K"

# Tier ladder: all N×N square layouts, keeping cell ratio equal to whole-image ratio and center-crop nearly a no-op.
# Order is tier-selection order: (cell_count, grid_size, side).
_GRID_LADDER: tuple[tuple[int, str, int], ...] = (
    (4, "grid_4", 2),
    (9, "grid_9", 3),
    (16, "grid_16", 4),
    (25, "grid_25", 5),
)

# Whole-image ratio directly uses canonical project video orientation; cell ratios match
_ORIENTATION_ASPECT: dict[str, str] = {"horizontal": "16:9", "vertical": "9:16"}

# Persisted whole-image ratios for legacy grid_6 (formerly the sole nonsquare tier), indexed by (rows, cols)
_LEGACY_NON_SQUARE_ASPECT: dict[tuple[int, int], str] = {(3, 2): "4:3", (2, 3): "3:4"}

_GATED_MAX_CELL_COUNT = 9
_MAX_CELL_COUNT = 25


def large_grid_allowed(image_resolution: str | None) -> bool:
    """Whether 4×4 / 5×5 are available: only effective 4K image resolution.

    ``None`` omits SDK resolution (``docs/adr/0019``) and uses the fallback tier.
    """
    return (image_resolution or GRID_FALLBACK_RESOLUTION).strip().upper() == _LARGE_GRID_RESOLUTION


def max_cell_count(*, allow_large_grid: bool) -> int:
    """Maximum cells per grid; callers split oversized groups at this bound."""
    return _MAX_CELL_COUNT if allow_large_grid else _GATED_MAX_CELL_COUNT


def video_aspect_ratio_of(project: dict[str, Any]) -> str:
    """Project video ratio, shared by grid canvases and recorded frozen cell ratios.

    project.json aspect_ratio permits explicit null (Pydantic str | None); dict.get(key, default) does
    not replace existing None values. Explicitly check absence, or None propagates into canvas geometry,
    prompts, and frozen record values.
    """
    raw = project.get("aspect_ratio")
    return raw if raw is not None else _ORIENTATION_ASPECT["vertical"]


def _orientation_of(aspect_ratio: str) -> str:
    """Determine orientation by comparing width and height numerically."""
    parts = aspect_ratio.split(":")
    w_ratio, h_ratio = int(parts[0]), int(parts[1])
    return "horizontal" if w_ratio > h_ratio else "vertical"


def grid_aspect_ratio_for(rows: int, cols: int, aspect_ratio: str) -> str:
    """Whole-image ratio from a record's rows/cols, used for legacy regeneration.

    Square tiers (rows == cols) equal the video ratio, matching :func:`calculate_grid_layout`.
    Nonsquare records can only be legacy grid_6; use their original persisted ratio. Regeneration retains
    frozen prompts, so request ratios must match described canvases. Deriving geometrically consistent ratios
    from cells (3×2 → 32:27) contradicts prompts and backend size tables, silently falling back to near-square images.
    Unregistered nonsquare geometry falls back to video ratio, likewise recognized by all backends.
    """
    video_aspect = _ORIENTATION_ASPECT[_orientation_of(aspect_ratio)]
    if rows == cols:
        return video_aspect
    return _LEGACY_NON_SQUARE_ASPECT.get((rows, cols), video_aspect)


def calculate_grid_layout(num_scenes: int, aspect_ratio: str, *, allow_large_grid: bool = False) -> GridLayout | None:
    """Calculate the appropriate grid layout for the given number of scenes.

    Args:
        num_scenes: Number of scenes to display in the grid.
        aspect_ratio: Aspect ratio string (e.g. "16:9", "9:16", "4:3").
        allow_large_grid: permit 4×4 / 5×5 tiers (callers resolve effective resolution through
            :func:`large_grid_allowed`). Default false; unspecified calls cap at 3×3.

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


def plan_grid_chunks[T](
    group: Sequence[T],
    aspect_ratio: str,
    *,
    allow_large_grid: bool = False,
) -> list[tuple[list[T], GridLayout]]:
    """Split scene groups at the per-grid cell limit; return chunks and layouts.

    Sole “group → :func:`max_cell_count` chunks → layout” implementation, shared by enqueueing, previews,
    and quoting so grid counts/tiers cannot drift. Choose the first tier from whole-group size; undersized
    final chunks use smaller tiers with placeholder cells. Chunks never overlap, their union is the group,
    and original order remains (first/tail-frame chains are within chunks, never across chunks).

    Empty groups return an empty list.
    """
    layout = calculate_grid_layout(len(group), aspect_ratio, allow_large_grid=allow_large_grid)
    if layout is None:
        return []
    plans: list[tuple[list[T], GridLayout]] = []
    for i in range(0, len(group), layout.cell_count):
        chunk = list(group[i : i + layout.cell_count])
        chunk_layout = calculate_grid_layout(len(chunk), aspect_ratio, allow_large_grid=allow_large_grid)
        if chunk_layout is None:  # pragma: no cover -- chunk is always nonempty; unreachable
            continue
        plans.append((chunk, chunk_layout))
    return plans
