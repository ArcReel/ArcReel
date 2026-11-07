"""OpenAIImageBackend prioritizes aspect ratios, then resolution.

- image_size=None still sends exact ratios with 720P short-edge defaults, never omits size;
- Tier values supply short edges, aspect_ratio supplies shape, and quality accompanies them;
- Custom WxH contributes minimum short edge only, discarding embedded ratios in favor of aspect_ratio;
- I2I images.edit forwards size symmetrically with T2I.
"""

from typing import ClassVar
from unittest.mock import MagicMock

import pytest

from lib.backends.image_backends.base import ImageCapability, ImageGenerationRequest, ReferenceImage
from lib.backends.image_backends.openai import OpenAIImageBackend


def _make_backend():
    backend = OpenAIImageBackend.__new__(OpenAIImageBackend)
    backend._client = MagicMock()
    backend._model = "gpt-image-2"
    # Full default mode="both" lets generate() capability gates admit T2I/I2I.
    backend._capabilities = {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
    return backend


def _stub_generate(backend) -> dict:
    captured: dict = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)

        class FakeResp:
            data: ClassVar[list[object]] = [type("D", (), {"b64_json": "aGk="})()]

        return FakeResp()

    backend._client.images.generate = fake_generate
    return captured


def _stub_edit(backend) -> dict:
    captured: dict = {}

    async def fake_edit(**kwargs):
        captured.update(kwargs)

        class FakeResp:
            data: ClassVar[list[object]] = [type("D", (), {"b64_json": "aGk="})()]

        return FakeResp()

    backend._client.images.edit = fake_edit
    return captured


@pytest.mark.asyncio
async def test_image_size_none_still_sends_ratio_correct_size(tmp_path):
    """image_size=None sends 720P fallback exact ratios, fixing former omitted-size ratio loss."""
    backend = _make_backend()
    captured = _stub_generate(backend)

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size=None,
    )
    await backend.generate(req)

    assert captured["size"] == "720x1280"
    assert "quality" not in captured  # None has no tier, so omit quality.


@pytest.mark.asyncio
async def test_image_size_tier_maps_to_short_edge(tmp_path):
    backend = _make_backend()
    captured = _stub_generate(backend)

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="1K",
    )
    await backend.generate(req)

    assert captured["size"] == "1008x1792"  # 1K short edge 1024 → exact 9:16.
    assert captured["quality"] == "medium"


@pytest.mark.asyncio
async def test_custom_wh_strips_embedded_ratio(tmp_path):
    """Custom WxH contributes min short edges only; aspect_ratio still determines shape, never 1920x1080 embedded 16:9."""
    backend = _make_backend()
    captured = _stub_generate(backend)

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="1920x1080",  # Input 16:9, project 9:16.
    )
    await backend.generate(req)

    w, h = (int(x) for x in captured["size"].split("x"))
    assert w * 16 == h * 9  # Exact 9:16, never input 16:9.
    assert w < h  # Portrait
    assert "quality" not in captured  # Custom values have no tier mapping.


@pytest.mark.asyncio
async def test_noncanonical_tier_casing_still_maps_quality(tmp_path):
    """Noncanonical case/whitespace tiers such as 2k must parse both size/quality, never lose quality after valid sizing."""
    backend = _make_backend()
    captured = _stub_generate(backend)

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="2k ",  # Lowercase plus trailing whitespace.
    )
    await backend.generate(req)

    assert captured["size"] == "1440x2560"
    assert captured["quality"] == "high"


@pytest.mark.asyncio
async def test_2k_tier(tmp_path):
    backend = _make_backend()
    captured = _stub_generate(backend)

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="2K",
    )
    await backend.generate(req)

    assert captured["size"] == "1440x2560"  # 2K short edge 1440.
    assert captured["quality"] == "high"


@pytest.mark.asyncio
async def test_i2i_edit_sends_size_symmetric_with_t2i(tmp_path):
    """I2I images.edit also forwards size, fixing user-observed I2I ratio loss."""
    backend = _make_backend()
    captured = _stub_edit(backend)

    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)

    req = ImageGenerationRequest(
        prompt="edit",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="1K",
        reference_images=[ReferenceImage(path=str(ref))],
    )
    await backend.generate(req)

    assert captured["size"] == "1008x1792"
    assert captured["quality"] == "medium"
