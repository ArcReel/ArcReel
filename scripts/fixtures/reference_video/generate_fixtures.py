"""Generate solid-color SDK references, cross-platform without external assets."""

from __future__ import annotations

import colorsys
from pathlib import Path

from PIL import Image


def generate_color_refs(
    out_dir: Path,
    *,
    count: int,
    size: tuple[int, int] = (512, 512),
) -> list[Path]:
    """Generate count evenly spaced-hue PNGs under out_dir and return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(count):
        hue = i / max(count, 1)
        rgb = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.7, 0.95))
        out = out_dir / f"ref_{i + 1}.png"
        with Image.new("RGB", size, rgb) as img:
            img.save(out, format="PNG")
        paths.append(out)
    return paths
