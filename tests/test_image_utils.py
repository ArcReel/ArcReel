# tests/test_image_utils.py
"""image_utils 单元测试。"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from lib.image_utils import compress_image_bytes, normalize_uploaded_image


class TestCompressImageBytes:
    """compress_image_bytes 测试。"""

    def _make_png(self, width: int, height: int) -> bytes:
        """生成指定尺寸的 PNG 字节。"""
        img = Image.new("RGB", (width, height), color="red")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_small_image_unchanged_dimensions(self):
        """小图（长边 < 2048）不缩放，但仍转为 JPEG。"""
        raw = self._make_png(800, 600)
        result = compress_image_bytes(raw)
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert img.size == (800, 600)

    def test_large_image_resized(self):
        """大图（长边 > 2048）缩放到长边 2048。"""
        raw = self._make_png(4096, 3072)
        result = compress_image_bytes(raw)
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert max(img.size) == 2048
        assert img.size == (2048, 1536)

    def test_portrait_large_image(self):
        """竖图大图也正确缩放。"""
        raw = self._make_png(2000, 4000)
        result = compress_image_bytes(raw)
        img = Image.open(BytesIO(result))
        assert max(img.size) == 2048
        assert img.size == (1024, 2048)

    def test_rgba_converted_to_rgb(self):
        """RGBA 图片转为 RGB（JPEG 不支持 alpha）。"""
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        buf = BytesIO()
        img.save(buf, format="PNG")
        result = compress_image_bytes(buf.getvalue())
        out = Image.open(BytesIO(result))
        assert out.mode == "RGB"

    def test_jpeg_input(self):
        """JPEG 输入也能正常处理。"""
        img = Image.new("RGB", (500, 500), color="blue")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=95)
        result = compress_image_bytes(buf.getvalue())
        out = Image.open(BytesIO(result))
        assert out.format == "JPEG"

    def test_webp_input(self):
        """WebP 输入也能正常处理。"""
        img = Image.new("RGB", (500, 500), color="green")
        buf = BytesIO()
        img.save(buf, format="WEBP")
        result = compress_image_bytes(buf.getvalue())
        out = Image.open(BytesIO(result))
        assert out.format == "JPEG"

    def test_invalid_input_raises(self):
        """非图片字节抛出 ValueError。"""
        with pytest.raises(ValueError, match="Invalid image"):
            compress_image_bytes(b"not an image")

    def test_output_smaller_than_input(self):
        """压缩后体积应显著减小。"""
        raw = self._make_png(3000, 2000)
        result = compress_image_bytes(raw)
        assert len(result) < len(raw)

    def _make_noise_png(self, width: int, height: int) -> bytes:
        img = Image.effect_noise((width, height), 80).convert("RGB")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_default_subsampling_unchanged(self):
        """缺省（sentinel -1）不向 PIL 传 subsampling，与历史行为一致。"""
        raw = self._make_png(800, 600)
        # 缺省 == 显式 sentinel == 不传 subsampling
        assert compress_image_bytes(raw) == compress_image_bytes(raw, subsampling=-1)

    def test_subsampling_0_valid_and_takes_effect(self):
        """subsampling=0（4:4:4）合法可解；对噪声图体积不小于默认 4:2:0（证明参数生效）。"""
        raw = self._make_noise_png(1024, 1024)
        out_444 = compress_image_bytes(raw, subsampling=0)
        img = Image.open(BytesIO(out_444))
        assert img.format == "JPEG"
        out_default = compress_image_bytes(raw)
        assert len(out_444) >= len(out_default)

    def test_normalize_uploaded_image_still_default(self):
        """normalize_uploaded_image 不传 subsampling，保持默认（回归）。"""
        big = self._make_noise_png(3000, 2000)  # 噪声 PNG，体积 > 2MB 阈值，触发压缩分支
        assert len(big) > 2 * 1024 * 1024
        processed, suffix = normalize_uploaded_image(big, ".png")
        assert suffix == ".jpg"
        # 与默认 compress_image_bytes 字节一致（未引入 subsampling 漂移）
        assert processed == compress_image_bytes(big)

    def test_normalize_uploaded_image_small_passthrough(self):
        """小图不超阈值时原样透传，保留原 suffix。"""
        small = self._make_png(100, 100)
        processed, suffix = normalize_uploaded_image(small, ".png")
        assert processed == small
        assert suffix == ".png"
