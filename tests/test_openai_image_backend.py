"""OpenAIImageBackend 单元测试。"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.providers import PROVIDER_OPENAI


def _make_mock_image_response(b64_data: str = "aW1hZ2VfZGF0YQ=="):
    """构造 mock ImagesResponse。"""
    datum = MagicMock()
    datum.b64_json = b64_data

    response = MagicMock()
    response.data = [datum]
    return response


class TestOpenAIImageBackend:
    def test_name_and_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            assert backend.name == PROVIDER_OPENAI
            assert backend.model == "gpt-image-1.5"

    def test_custom_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key", model="gpt-image-1-mini")
            assert backend.model == "gpt-image-1-mini"

    def test_capabilities(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            assert ImageCapability.TEXT_TO_IMAGE in backend.capabilities
            assert ImageCapability.IMAGE_TO_IMAGE in backend.capabilities

    async def test_text_to_image(self, tmp_path: Path):
        """T2I 路径应调用 images.generate()。"""
        b64_data = base64.b64encode(b"fake-png-data").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(b64_data)
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            output_path = tmp_path / "output.png"
            request = ImageGenerationRequest(
                prompt="A beautiful sunset",
                output_path=output_path,
                aspect_ratio="9:16",
                image_size="1K",
            )
            result = await backend.generate(request)

        assert result.provider == PROVIDER_OPENAI
        assert result.model == "gpt-image-1.5"
        assert result.image_path == output_path
        assert output_path.read_bytes() == b"fake-png-data"

        mock_client.images.generate.assert_awaited_once()
        call_kwargs = mock_client.images.generate.call_args[1]
        assert call_kwargs["model"] == "gpt-image-1.5"
        assert call_kwargs["size"] == "1024x1792"  # 9:16
        assert call_kwargs["quality"] == "medium"   # 1K
        assert call_kwargs["response_format"] == "b64_json"

    async def test_image_to_image(self, tmp_path: Path):
        """I2I 路径应调用 images.edit()。"""
        b64_data = base64.b64encode(b"edited-image").decode()
        mock_client = AsyncMock()
        mock_client.images.edit = AsyncMock(
            return_value=_make_mock_image_response(b64_data)
        )

        ref_path = tmp_path / "ref.png"
        ref_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            output_path = tmp_path / "output.png"
            request = ImageGenerationRequest(
                prompt="Edit this image",
                output_path=output_path,
                reference_images=[ReferenceImage(path=str(ref_path))],
            )
            result = await backend.generate(request)

        assert result.image_path == output_path
        assert output_path.read_bytes() == b"edited-image"
        mock_client.images.edit.assert_awaited_once()
        mock_client.images.generate.assert_not_awaited()

    async def test_size_mapping(self, tmp_path: Path):
        """验证 aspect_ratio → size 映射。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(b64_data)
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")

            for aspect, expected_size in [("16:9", "1792x1024"), ("1:1", "1024x1024"), ("9:16", "1024x1792")]:
                output_path = tmp_path / f"output_{aspect.replace(':', '_')}.png"
                request = ImageGenerationRequest(
                    prompt="test", output_path=output_path, aspect_ratio=aspect,
                )
                await backend.generate(request)
                call_kwargs = mock_client.images.generate.call_args[1]
                assert call_kwargs["size"] == expected_size, f"aspect={aspect}"

    async def test_quality_mapping(self, tmp_path: Path):
        """验证 image_size → quality 映射。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(b64_data)
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")

            for img_size, expected_quality in [("512PX", "low"), ("1K", "medium"), ("2K", "high"), ("4K", "high")]:
                output_path = tmp_path / f"output_{img_size}.png"
                request = ImageGenerationRequest(
                    prompt="test", output_path=output_path, image_size=img_size,
                )
                await backend.generate(request)
                call_kwargs = mock_client.images.generate.call_args[1]
                assert call_kwargs["quality"] == expected_quality, f"size={img_size}"
