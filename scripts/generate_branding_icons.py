"""Generate branding icons for white-label deployments.

默认仓库不使用本脚本运行后的产物（仓库里沉淀的是上游 ArcReel 原图标），
仅在为客户白标部署时运行以生成一套高低调的默认图标。

输出位置: frontend/public/
- favicon-16x16.png / favicon-32x32.png / favicon.ico
- apple-touch-icon.png (180x180)
- android-chrome-{192,512}x{...}.png
- android-chrome-maskable-{192,512}x{...}.png

设计：
- 圆角方形（圆角 = 22% 边长，maskable 版本边到边）
- 渐变背景：indigo (#4F46E5) → violet (#9333EA)，斜对角
- 前景：粗体白色首字母（默认从 $VITE_BRAND_NAME 取，或用 --letter 参数）

用法：
  VITE_BRAND_NAME=StoryFlow uv run python scripts/generate_branding_icons.py
  uv run python scripts/generate_branding_icons.py --letter S
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public"

# 渐变端点
GRADIENT_START = (79, 70, 229)  # indigo-600
GRADIENT_END = (147, 51, 234)  # violet-600
FG_COLOR = (255, 255, 255, 255)


def _candidate_fonts() -> list[Path]:
    """优先寻找系统中真正粗体的无衬线字体（避免变体字体默认 Regular 权重导致笔画太细）。"""
    return [
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Helvetica.ttc"),
        Path("/Library/Fonts/Arial Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/System/Library/Fonts/SFNS.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]


def _try_set_bold(font: ImageFont.FreeTypeFont) -> ImageFont.FreeTypeFont:
    """如果字体是变体字体（例如 SFNS.ttf），尝试切换到 Bold 命名变体。"""
    try:
        names = font.get_variation_names()  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return font
    for cand in (b"Bold", b"Heavy", b"Black", b"Semibold"):
        if cand in names:
            try:
                font.set_variation_by_name(cand)  # type: ignore[attr-defined]
            except OSError:
                continue
            break
    return font


def _gradient_square(size: int) -> Image.Image:
    """生成对角渐变方块（不带圆角）。"""
    base = Image.new("RGB", (size, size), GRADIENT_START)
    pixels = base.load()
    assert pixels is not None
    # 沿对角线 (i+j) / (2*(size-1)) 计算插值因子
    denom = max(2 * (size - 1), 1)
    for y in range(size):
        for x in range(size):
            t = (x + y) / denom
            r = int(GRADIENT_START[0] + (GRADIENT_END[0] - GRADIENT_START[0]) * t)
            g = int(GRADIENT_START[1] + (GRADIENT_END[1] - GRADIENT_START[1]) * t)
            b = int(GRADIENT_START[2] + (GRADIENT_END[2] - GRADIENT_START[2]) * t)
            pixels[x, y] = (r, g, b)
    return base


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def _fit_letter_size(area_h: int, letter: str, font_path: Path | None) -> int:
    """返回能让指定字母实际渲染高度 ≈ area_h 的字号。

    不同字体的 cap-height / em 比例差异很大，直接用 0.72 估算容易出血。
    步骤：先以 area_h 为字号试画一次，量出实际 bbox 高度，再反推目标字号。
    """
    probe_size = max(area_h, 8)
    if font_path is not None and font_path.exists():
        font = ImageFont.truetype(str(font_path), size=probe_size)
        font = _try_set_bold(font)
    else:
        font = ImageFont.load_default()
    tmp = Image.new("L", (probe_size * 2, probe_size * 2))
    draw = ImageDraw.Draw(tmp)
    bbox = draw.textbbox((0, 0), letter, font=font, anchor="lt")
    actual_h = max(bbox[3] - bbox[1], 1)
    return max(int(probe_size * area_h / actual_h), 8)


def _draw_letter(canvas: Image.Image, area: tuple[int, int, int, int], letter: str) -> None:
    """在 area (x0, y0, x1, y1) 区域内居中绘制白色粗体字母。"""
    x0, y0, x1, y1 = area
    target_h = y1 - y0
    font_path = next((p for p in _candidate_fonts() if p.exists()), None)
    font_size = _fit_letter_size(target_h, letter, font_path)
    if font_path is not None:
        font = ImageFont.truetype(str(font_path), size=font_size)
        font = _try_set_bold(font)
    else:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), letter, font=font, anchor="lt")
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    cx = (x0 + x1) / 2 - bbox[0] - text_w / 2
    # 视觉居中：字母上半部更"重"，几何居中会显得偏低；向上偏移约 16% 区域高度。
    cy = (y0 + y1) / 2 - bbox[1] - text_h / 2 - (y1 - y0) * 0.16
    draw.text((cx, cy), letter, font=font, fill=FG_COLOR)


def make_icon(size: int, letter: str, *, maskable: bool = False) -> Image.Image:
    """生成单张图标。“maskable=True”：边到边纯色（无圆角透明），图形内容缩到 ~60% 安全区。"""
    bg = _gradient_square(size)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    if maskable:
        canvas.paste(bg, (0, 0))
        safe = int(size * 0.20)
        _draw_letter(canvas, (safe, safe, size - safe, size - safe), letter)
    else:
        radius = int(size * 0.22)
        mask = _rounded_mask(size, radius)
        canvas.paste(bg, (0, 0), mask=mask)
        pad = int(size * 0.22)
        _draw_letter(canvas, (pad, pad, size - pad, size - pad), letter)

    return canvas


def write_png(image: Image.Image, name: str) -> None:
    out_path = OUTPUT_DIR / name
    image.save(out_path, format="PNG")
    print(f"  wrote {out_path.relative_to(OUTPUT_DIR.parent.parent)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--letter",
        help="图标上的字母；默认从 $VITE_BRAND_NAME 取首字母，均未设置则为 'A'",
    )
    args = parser.parse_args()

    brand = os.environ.get("VITE_BRAND_NAME", "").strip()
    letter = (args.letter or (brand[:1] if brand else "A")).upper()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Generating icons (letter='{letter}') into {OUTPUT_DIR}")

    sizes = {
        "favicon-16x16.png": (16, False),
        "favicon-32x32.png": (32, False),
        "apple-touch-icon.png": (180, False),
        "android-chrome-192x192.png": (192, False),
        "android-chrome-512x512.png": (512, False),
        "android-chrome-maskable-192x192.png": (192, True),
        "android-chrome-maskable-512x512.png": (512, True),
    }
    for name, (size, maskable) in sizes.items():
        write_png(make_icon(size, letter, maskable=maskable), name)

    # favicon.ico —— 多尺寸合并
    ico_sizes = [(16, 16), (32, 32), (48, 48)]
    base = make_icon(256, letter, maskable=False)
    ico_path = OUTPUT_DIR / "favicon.ico"
    base.save(ico_path, format="ICO", sizes=ico_sizes)
    print(f"  wrote {ico_path.relative_to(OUTPUT_DIR.parent.parent)}")
    print("Done.")


if __name__ == "__main__":
    main()
