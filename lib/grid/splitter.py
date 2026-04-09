"""Grid image splitter utilities."""

from __future__ import annotations

import numpy as np
from PIL import Image


def center_crop_to_ratio(img: Image.Image, target_ratio: str) -> Image.Image:
    """Crop image from center to match the target aspect ratio.

    Args:
        img: Source PIL image.
        target_ratio: Aspect ratio string like "16:9" or "9:16".

    Returns:
        Cropped image with the desired aspect ratio.
    """
    w_str, h_str = target_ratio.split(":")
    w_ratio = float(w_str)
    h_ratio = float(h_str)

    w, h = img.size
    current_ratio = w / h
    target = w_ratio / h_ratio

    if abs(current_ratio - target) < 0.01:
        return img

    if current_ratio > target:
        # Too wide — crop width
        new_w = int(h * target)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        # Too tall — crop height
        new_h = int(w / target)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))


def is_placeholder_cell(img: Image.Image, threshold: float = 0.90) -> bool:
    """Detect whether an image cell is a placeholder (solid gray/black).

    Samples the center 50% of the image and checks if it has low variance
    and low mean brightness.

    Args:
        img: PIL image to check.
        threshold: Unused parameter kept for API compatibility.

    Returns:
        True if the cell appears to be a placeholder.
    """
    w, h = img.size
    left = w // 4
    top = h // 4
    right = w - w // 4
    bottom = h - h // 4
    center = img.crop((left, top, right, bottom))

    arr = np.array(center, dtype=np.float32)
    std = float(arr.std())
    mean = float(arr.mean())

    return std < 15 and mean < 160


def split_grid_image(
    grid_image: Image.Image,
    rows: int,
    cols: int,
    video_aspect_ratio: str,
    edge_margin: float = 0.02,
) -> list[Image.Image]:
    """Split a grid image into individual cell images.

    Cells are returned in row-major order (left-to-right, top-to-bottom).
    Each cell has edge margins trimmed and is then center-cropped to the
    requested aspect ratio.

    Args:
        grid_image: Source grid PIL image.
        rows: Number of rows in the grid.
        cols: Number of columns in the grid.
        video_aspect_ratio: Target aspect ratio string, e.g. "16:9".
        edge_margin: Fraction of cell size to trim from each edge (0.0–0.5).

    Returns:
        List of cropped cell images.
    """
    grid_w, grid_h = grid_image.size
    cell_w = grid_w // cols
    cell_h = grid_h // rows

    margin_x = int(cell_w * edge_margin)
    margin_y = int(cell_h * edge_margin)

    cells: list[Image.Image] = []
    for row in range(rows):
        for col in range(cols):
            x0 = col * cell_w
            y0 = row * cell_h
            x1 = x0 + cell_w
            y1 = y0 + cell_h

            cell = grid_image.crop((x0, y0, x1, y1))

            # Apply edge margin trim
            if margin_x > 0 or margin_y > 0:
                cw, ch = cell.size
                cell = cell.crop((margin_x, margin_y, cw - margin_x, ch - margin_y))

            # Center crop to target aspect ratio
            cell = center_crop_to_ratio(cell, video_aspect_ratio)
            cells.append(cell)

    return cells
