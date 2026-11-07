"""OpenAI image quality dimensions map from image_size, send with requests, and report in usage selections."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from tests.fakes import captured_openai_clients


def _quality(result) -> object:
    assert result.usage is not None
    return result.usage.dimensions.get("quality")


def _make_mock_image_response(b64_data: str = "aW1hZ2VfZGF0YQ=="):
    """Build mocked ImagesResponse."""
    datum = MagicMock()
    datum.b64_json = b64_data
    response = MagicMock()
    response.data = [datum]
    return response


class TestOpenAIImageBackendQuality:
    async def test_generate_result_contains_quality(self, tmp_path: Path):
        """generate() usage reports actual request quality selections."""
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
        """All image_size values map correctly to quality dimensions."""
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
        """Unknown image_size never falls back to medium; omit quality from requests/usage."""
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
