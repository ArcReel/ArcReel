# tests/test_image_utils.py
"""image_utils unit tests."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from lib.infra import image_utils
from lib.infra.image_utils import compress_image_bytes, normalize_storyboard_upload, normalize_uploaded_image


class TestCompressImageBytes:
    """compress_image_bytes tests."""

    def _make_png(self, width: int, height: int) -> bytes:
        """Create PNG bytes at specified dimensions."""
        img = Image.new("RGB", (width, height), color="red")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_small_image_unchanged_dimensions(self):
        """Small images, longest edge < 2048, keep dimensions but still convert to JPEG."""
        raw = self._make_png(800, 600)
        result = compress_image_bytes(raw)
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert img.size == (800, 600)

    def test_large_image_resized(self):
        """Large images, longest edge > 2048, scale to edge 2048."""
        raw = self._make_png(4096, 3072)
        result = compress_image_bytes(raw)
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert max(img.size) == 2048
        assert img.size == (2048, 1536)

    def test_portrait_large_image(self):
        """Large portrait images also scale correctly."""
        raw = self._make_png(2000, 4000)
        result = compress_image_bytes(raw)
        img = Image.open(BytesIO(result))
        assert max(img.size) == 2048
        assert img.size == (1024, 2048)

    def test_rgba_converted_to_rgb(self):
        """Convert RGBA to RGB because JPEG cannot carry alpha."""
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        buf = BytesIO()
        img.save(buf, format="PNG")
        result = compress_image_bytes(buf.getvalue())
        out = Image.open(BytesIO(result))
        assert out.mode == "RGB"

    def test_jpeg_input(self):
        """JPEG inputs work normally."""
        img = Image.new("RGB", (500, 500), color="blue")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=95)
        result = compress_image_bytes(buf.getvalue())
        out = Image.open(BytesIO(result))
        assert out.format == "JPEG"

    def test_webp_input(self):
        """WebP inputs work normally."""
        img = Image.new("RGB", (500, 500), color="green")
        buf = BytesIO()
        img.save(buf, format="WEBP")
        result = compress_image_bytes(buf.getvalue())
        out = Image.open(BytesIO(result))
        assert out.format == "JPEG"

    def test_invalid_input_raises(self):
        """Nonimage bytes raise ValueError."""
        with pytest.raises(ValueError, match="Invalid image"):
            compress_image_bytes(b"not an image")

    def test_output_smaller_than_input(self):
        """Compression substantially reduces size."""
        raw = self._make_png(3000, 2000)
        result = compress_image_bytes(raw)
        assert len(result) < len(raw)

    def _make_noise_png(self, width: int, height: int) -> bytes:
        img = Image.effect_noise((width, height), 80).convert("RGB")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_default_subsampling_unchanged(self):
        """Default sentinel -1 omits PIL subsampling, matching historical behavior."""
        raw = self._make_png(800, 600)
        # Default == explicit sentinel == omitted subsampling.
        assert compress_image_bytes(raw) == compress_image_bytes(raw, subsampling=-1)

    def test_subsampling_0_valid_and_takes_effect(self):
        """subsampling=0 (4:4:4) decodes validly; noise images are no smaller than default 4:2:0, proving application."""
        raw = self._make_noise_png(1024, 1024)
        out_444 = compress_image_bytes(raw, subsampling=0)
        img = Image.open(BytesIO(out_444))
        assert img.format == "JPEG"
        out_default = compress_image_bytes(raw)
        assert len(out_444) >= len(out_default)

    def test_normalize_uploaded_image_still_default(self):
        """normalize_uploaded_image omits subsampling, retaining defaults (regression)."""
        big = self._make_noise_png(3000, 2000)  # Noise PNG above the 2MB threshold triggers compression.
        assert len(big) > 2 * 1024 * 1024
        processed, suffix = normalize_uploaded_image(big, ".png")
        assert suffix == ".jpg"
        # Bytes match default compress_image_bytes, without subsampling drift.
        assert processed == compress_image_bytes(big)

    def test_normalize_uploaded_image_small_passthrough(self):
        """Small images below thresholds pass unchanged with original suffixes."""
        small = self._make_png(100, 100)
        processed, suffix = normalize_uploaded_image(small, ".png")
        assert processed == small
        assert suffix == ".png"


class TestUploadPixelBudget:
    """Decoded-pixel caps cover regions beyond byte limits/PIL bomb checks."""

    def _png(self, width: int, height: int) -> bytes:
        buf = BytesIO()
        Image.new("RGB", (width, height), color="red").save(buf, format="PNG")
        return buf.getvalue()

    def test_rejects_image_over_pixel_budget(self, monkeypatch):
        """Reject images exceeding caps, distinguishing them from unparseable inputs.

        Lower caps through monkeypatch: real boundary images need over a hundred million pixels
        and cost too much to construct; decisions read header dimensions independently of thresholds.
        """
        monkeypatch.setattr(image_utils, "MAX_UPLOAD_PIXELS", 100 * 100 - 1)
        with pytest.raises(image_utils.ImagePixelLimitError):
            normalize_storyboard_upload(self._png(100, 100), max_long_edge=None)

    def test_accepts_image_at_budget(self, monkeypatch):
        """Admit images exactly at caps, without overrestricting boundaries."""
        monkeypatch.setattr(image_utils, "MAX_UPLOAD_PIXELS", 100 * 100)
        assert normalize_storyboard_upload(self._png(100, 100), max_long_edge=None)

    def test_pixel_limit_is_a_value_error(self):
        """Subclass ValueError so callers without specialized handling still classify invalid images."""
        assert issubclass(image_utils.ImagePixelLimitError, ValueError)

    def test_grid_sized_composite_passes_default_budget(self):
        """4K-square composites remain within default caps, never falsely rejected."""
        assert image_utils.MAX_UPLOAD_PIXELS > 4096 * 4096
