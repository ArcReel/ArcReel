"""测试 GrokImageBackend 对 image_size 的新逻辑：None 不传，非 None 转小写下发。"""

from unittest.mock import MagicMock

import pytest

from lib.backends.image_backends.base import ImageGenerationRequest
from lib.backends.image_backends.grok import GrokImageBackend


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
async def test_image_size_lowercased_for_sdk(tmp_path):
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

    # 目录取值 "2K" 下发前转成 xai-sdk 接受的小写 "2k"
    assert captured["resolution"] == "2k"
