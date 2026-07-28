"""Grid layout calculator for grid-image-to-video feature."""

from __future__ import annotations

from dataclasses import dataclass

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


# Grid configuration: {grid_size: {orientation: (rows, cols, aspect_ratio)}}
_GRID_CONFIGS: dict[str, dict[str, tuple[int, int, str]]] = {
    "grid_4": {
        "horizontal": (2, 2, "16:9"),
        "vertical": (2, 2, "9:16"),
    },
    "grid_6": {
        "horizontal": (3, 2, "4:3"),
        "vertical": (2, 3, "3:4"),
    },
    "grid_9": {
        "horizontal": (3, 3, "16:9"),
        "vertical": (3, 3, "9:16"),
    },
}


def calculate_grid_layout(num_scenes: int, aspect_ratio: str) -> GridLayout | None:
    """Calculate the appropriate grid layout for the given number of scenes.

    Args:
        num_scenes: Number of scenes to display in the grid.
        aspect_ratio: Aspect ratio string (e.g. "16:9", "9:16", "4:3").

    Returns:
        GridLayout if num_scenes >= 4, otherwise None.
    """
    if num_scenes < 1:
        return None

    effective = min(num_scenes, 9)

    if effective <= 4:
        grid_size = "grid_4"
        cell_count = 4
    elif effective <= 6:
        grid_size = "grid_6"
        cell_count = 6
    else:
        grid_size = "grid_9"
        cell_count = 9

    # Determine orientation by comparing width and height numerically
    parts = aspect_ratio.split(":")
    w_ratio, h_ratio = int(parts[0]), int(parts[1])
    orientation = "horizontal" if w_ratio > h_ratio else "vertical"

    rows, cols, grid_aspect_ratio = _GRID_CONFIGS[grid_size][orientation]
    placeholder_count = cell_count - effective

    return GridLayout(
        grid_size=grid_size,
        rows=rows,
        cols=cols,
        grid_aspect_ratio=grid_aspect_ratio,
        cell_count=cell_count,
        placeholder_count=placeholder_count,
    )
