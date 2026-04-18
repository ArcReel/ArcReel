from __future__ import annotations

import io

import pytest
from PIL import Image

from lib.image_utils import compress_image_bytes


def _make_big_png(width: int = 4096, height: int = 3072) -> bytes:
    img = Image.new("RGB", (width, height), color=(240, 80, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_compress_single_image_under_long_edge_2048():
    raw = _make_big_png()
    out = compress_image_bytes(raw, max_long_edge=2048, quality=85)
    with Image.open(io.BytesIO(out)) as im:
        assert max(im.size) <= 2048


def test_compress_batch_nine_images_memory_ok():
    """批量压缩 9 张 4K 图，检查每张输出尺寸与体积都符合预期。"""
    raw = _make_big_png()
    outputs = [compress_image_bytes(raw, max_long_edge=2048, quality=85) for _ in range(9)]
    assert len(outputs) == 9
    for out in outputs:
        # 压缩后体积显著小于原 PNG
        assert len(out) < len(raw)
        with Image.open(io.BytesIO(out)) as im:
            assert max(im.size) <= 2048


def test_compress_fallback_long_edge_1024_smaller_bytes():
    raw = _make_big_png()
    first = compress_image_bytes(raw, max_long_edge=2048, quality=85)
    second = compress_image_bytes(raw, max_long_edge=1024, quality=70)
    assert len(second) < len(first)
    with Image.open(io.BytesIO(second)) as im:
        assert max(im.size) <= 1024


def test_compress_rejects_invalid_bytes():
    with pytest.raises(ValueError):
        compress_image_bytes(b"not an image", max_long_edge=1024)
