"""测试 OpenAIImageBackend _SIZE_MAP 新语义：
- image_size=None → 不传 size / 不传 quality
- image_size=标准 token → 查 (image_size, aspect_ratio) 复合 key 得到 size，继续传 quality
- image_size=非标准 token → warning 后直接透传 size（由 SDK 校验）
"""

from unittest.mock import MagicMock

import pytest

from lib.image_backends.base import ImageCapability, ImageGenerationRequest
from lib.image_backends.openai import OpenAIImageBackend


def _make_backend():
    backend = OpenAIImageBackend.__new__(OpenAIImageBackend)
    backend._client = MagicMock()
    backend._model = "gpt-image-1.5"
    # 全能力（默认 mode="both"），让 generate() 的 capability gating 放行 T2I 与 I2I
    backend._capabilities = {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
    return backend


@pytest.mark.asyncio
async def test_image_size_none_omits_size_and_quality(tmp_path):
    backend = _make_backend()
    captured: dict = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)

        class FakeResp:
            data = [type("D", (), {"b64_json": "aGk="})()]

        return FakeResp()

    backend._client.images.generate = fake_generate

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size=None,
    )
    await backend.generate(req)

    assert "size" not in captured
    assert "quality" not in captured


@pytest.mark.asyncio
async def test_image_size_token_maps_to_size(tmp_path):
    backend = _make_backend()
    captured: dict = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)

        class FakeResp:
            data = [type("D", (), {"b64_json": "aGk="})()]

        return FakeResp()

    backend._client.images.generate = fake_generate

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="1K",
    )
    await backend.generate(req)

    assert captured["size"] == "1024x1792"
    assert captured["quality"] == "medium"


@pytest.mark.asyncio
async def test_unknown_image_size_passthrough_with_warning(tmp_path, caplog):
    backend = _make_backend()
    captured: dict = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)

        class FakeResp:
            data = [type("D", (), {"b64_json": "aGk="})()]

        return FakeResp()

    backend._client.images.generate = fake_generate

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="1024x1024",
    )
    await backend.generate(req)

    assert captured["size"] == "1024x1024"
    # quality 对未知 token 不传（没有映射）
    assert "quality" not in captured
