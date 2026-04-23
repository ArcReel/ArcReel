"""测试 GeminiImageBackend 对 image_size=None 的处理。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.image_backends.base import ImageGenerationRequest
from lib.image_backends.gemini import GeminiImageBackend


def _make_backend():
    backend = GeminiImageBackend.__new__(GeminiImageBackend)
    backend._rate_limiter = None
    backend._image_model = "gemini-3.1-flash-image-preview"
    backend._backend_type = "aistudio"
    backend._types = MagicMock()
    backend._client = MagicMock()
    backend._client.aio.models.generate_content = AsyncMock(return_value=MagicMock(parts=[]))
    backend._capabilities = set()
    return backend


@pytest.mark.asyncio
async def test_image_size_none_not_passed_to_image_config(tmp_path):
    backend = _make_backend()
    req = ImageGenerationRequest(
        prompt="hello",
        output_path=tmp_path / "out.png",
        aspect_ratio="9:16",
        image_size=None,
    )
    with pytest.raises(RuntimeError):  # 因 mocked response 返回空 parts
        await backend.generate(req)

    image_config_call = backend._types.ImageConfig.call_args
    assert "image_size" not in image_config_call.kwargs
    assert image_config_call.kwargs["aspect_ratio"] == "9:16"


@pytest.mark.asyncio
async def test_image_size_provided_is_passed_to_image_config(tmp_path):
    backend = _make_backend()
    req = ImageGenerationRequest(
        prompt="hello",
        output_path=tmp_path / "out.png",
        aspect_ratio="9:16",
        image_size="2K",
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    image_config_call = backend._types.ImageConfig.call_args
    assert image_config_call.kwargs["image_size"] == "2K"
