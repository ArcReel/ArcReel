from pathlib import Path

from PIL import Image

from scripts.fixtures.reference_video.generate_fixtures import generate_color_refs


def test_generate_color_refs_creates_n_pngs(tmp_path: Path):
    paths = generate_color_refs(tmp_path, count=3, size=(128, 128))
    assert len(paths) == 3
    for p in paths:
        assert p.suffix == ".png"
        assert p.exists()
        with Image.open(p) as img:
            assert img.size == (128, 128)


def test_generate_color_refs_distinct_colors(tmp_path: Path):
    paths = generate_color_refs(tmp_path, count=4)
    pixels = []
    for p in paths:
        with Image.open(p) as img:
            pixels.append(img.getpixel((0, 0)))
    assert len(set(pixels)) == 4  # 每张颜色不同
