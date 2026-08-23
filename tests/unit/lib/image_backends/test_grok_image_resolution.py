"""测试 GrokImageBackend 对 image_size 的新逻辑：None 不传，非 None 直接透传。"""

from unittest.mock import MagicMock

import pytest

from lib.image_backends.base import ImageGenerationRequest
from lib.image_backends.grok import GrokImageBackend


def _make_backend():
    backend = GrokImageBackend.__new__(GrokImageBackend)
    backend._client = MagicMock()
    backend._model = "grok-imagine-image"
    backend._capabilities = set()
    return backend


@pytest.mark.asyncio
async def test_image_size_none_not_in_kwargs(tmp_path):
    backend = _make_backend()
    captured: dict = {}

    async def fake_sample(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.image.sample = fake_sample
    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size=None,
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    assert "resolution" not in captured
    assert captured["aspect_ratio"] == "9:16"


@pytest.mark.asyncio
async def test_image_size_passed_through_as_is(tmp_path):
    backend = _make_backend()
    captured: dict = {}

    async def fake_sample(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.image.sample = fake_sample
    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="2K",
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    # 直接透传标准 token（不再经过 _map_image_size_to_resolution 小写映射）
    assert captured["resolution"] == "2K"
