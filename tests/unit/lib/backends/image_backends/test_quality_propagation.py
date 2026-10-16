"""OpenAI 图片的 quality 选择维度：按 image_size 映射出的 quality 随请求发送，并作为用量的选择维度上报。"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from tests.fakes import captured_openai_clients


def _quality(result) -> object:
    assert result.usage is not None
    return result.usage.dimensions.get("quality")


def _make_mock_image_response(b64_data: str = "aW1hZ2VfZGF0YQ=="):
    """构造 mock ImagesResponse。"""
    datum = MagicMock()
    datum.b64_json = b64_data
    response = MagicMock()
    response.data = [datum]
    return response


class TestOpenAIImageBackendQuality:
    async def test_generate_result_contains_quality(self, tmp_path: Path):
        """generate() 上报的用量带上请求里发送的 quality 维度。"""
        b64_data = base64.b64encode(b"fake-png").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.base import ImageGenerationRequest
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            output_path = tmp_path / "out.png"
            request = ImageGenerationRequest(
                prompt="test",
                output_path=output_path,
                image_size="1K",
            )
            result = await backend.generate(request)

        assert _quality(result) == "medium"

    async def test_quality_propagated_for_all_sizes(self, tmp_path: Path):
        """所有 image_size 值都应正确映射到 quality 维度。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.base import ImageGenerationRequest
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")

            expected = {
                "512px": "low",
                "1K": "medium",
                "2K": "high",
                "4K": "high",
            }
            for img_size, expected_quality in expected.items():
                output_path = tmp_path / f"out_{img_size}.png"
                request = ImageGenerationRequest(
                    prompt="test",
                    output_path=output_path,
                    image_size=img_size,
                )
                result = await backend.generate(request)
                assert _quality(result) == expected_quality, f"image_size={img_size}"

    async def test_unknown_size_result_quality_is_none(self, tmp_path: Path):
        """未知 image_size 不 fallback 到 'medium'，请求不发 quality，用量也不报这一维度。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.base import ImageGenerationRequest
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            output_path = tmp_path / "out_unknown.png"
            request = ImageGenerationRequest(
                prompt="test",
                output_path=output_path,
                image_size="UNKNOWN",
            )
            result = await backend.generate(request)

        assert _quality(result) is None
