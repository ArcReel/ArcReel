"""生成 SDK 验证用的纯色参考图（跨平台、无外部资产依赖）。"""

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
    """在 out_dir 下生成 count 张等间距色相的 PNG，返回路径列表。"""
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
