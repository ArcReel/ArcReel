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


def _make_mock_image_response(
    b64_data: str | None = "aW1hZ2VfZGF0YQ==",
    url: str | None = None,
    usage=None,
):
    """构造 mock ImagesResponse。

    - usage=None 时显式不挂 usage 属性（模拟 SDK 不返回 usage 的情况）
    - 传 dict {"input_tokens": ..., "output_tokens": ..., "input_tokens_details": {...}, "output_tokens_details": {...}}
      则按 dict 键构造嵌套 MagicMock
    """
    datum = MagicMock()
    datum.b64_json = b64_data
    datum.url = url

    # spec 限定属性集，避免 MagicMock 自动创造未知属性
    response = MagicMock(spec=["data", "usage"])
    response.data = [datum]
    if usage is None:
        response.usage = None
    else:
        usage_obj = MagicMock()
        usage_obj.input_tokens = usage.get("input_tokens")
        usage_obj.output_tokens = usage.get("output_tokens")
        in_d = usage.get("input_tokens_details")
        if in_d is None:
            usage_obj.input_tokens_details = None
        else:
            details = MagicMock()
            details.image_tokens = in_d.get("image_tokens")
            details.text_tokens = in_d.get("text_tokens")
            usage_obj.input_tokens_details = details
        out_d = usage.get("output_tokens_details")
        if out_d is None:
            usage_obj.output_tokens_details = None
        else:
            details = MagicMock()
            details.image_tokens = out_d.get("image_tokens")
            details.text_tokens = out_d.get("text_tokens")
            usage_obj.output_tokens_details = details
        response.usage = usage_obj
    return response


class TestOpenAIImageBackend:
    def test_name_and_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            assert backend.name == PROVIDER_OPENAI
            assert backend.model == "gpt-image-2"

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
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

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
        assert result.model == "gpt-image-2"
        assert result.image_path == output_path
        assert output_path.read_bytes() == b"fake-png-data"

        mock_client.images.generate.assert_awaited_once()
        call_kwargs = mock_client.images.generate.call_args[1]
        assert call_kwargs["model"] == "gpt-image-2"
        assert call_kwargs["size"] == "1024x1792"  # 9:16
        assert call_kwargs["quality"] == "medium"  # 1K
        # GPT Image 模型族不支持 response_format；严格兼容网关会 400，因此绝不能传
        assert "response_format" not in call_kwargs

    async def test_image_to_image(self, tmp_path: Path):
        """I2I 路径应调用 images.edit()。"""
        b64_data = base64.b64encode(b"edited-image").decode()
        mock_client = AsyncMock()
        mock_client.images.edit = AsyncMock(return_value=_make_mock_image_response(b64_data))

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
        edit_kwargs = mock_client.images.edit.call_args[1]
        assert "response_format" not in edit_kwargs

    async def test_text_to_image_url_fallback(self, tmp_path: Path):
        """网关只返回 url 时，应走 httpx 下载分支。"""
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(b64_data=None, url="https://gateway/img.png")
        )

        downloaded = b"downloaded-from-gateway"

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

            with patch("lib.image_backends.base.httpx.AsyncClient") as MockHttpClient:
                mock_http = AsyncMock()
                MockHttpClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
                MockHttpClient.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_resp = MagicMock()
                mock_resp.content = downloaded
                mock_resp.raise_for_status = MagicMock()
                mock_http.get = AsyncMock(return_value=mock_resp)

                result = await backend.generate(request)

            mock_http.get.assert_awaited_once_with("https://gateway/img.png", timeout=60)

        assert result.image_path == output_path
        assert output_path.read_bytes() == downloaded

    async def test_size_mapping(self, tmp_path: Path):
        """验证 (image_size, aspect_ratio) → size 复合 key 映射。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")

            # image_size="1K" 下遍历不同 aspect_ratio
            for aspect, expected_size in [("16:9", "1792x1024"), ("1:1", "1024x1024"), ("9:16", "1024x1792")]:
                output_path = tmp_path / f"output_{aspect.replace(':', '_')}.png"
                request = ImageGenerationRequest(
                    prompt="test",
                    output_path=output_path,
                    aspect_ratio=aspect,
                    image_size="1K",
                )
                await backend.generate(request)
                call_kwargs = mock_client.images.generate.call_args[1]
                assert call_kwargs["size"] == expected_size, f"aspect={aspect}"

    async def test_quality_mapping(self, tmp_path: Path):
        """验证 image_size → quality 映射（标准 token）。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")

            # "4K" 未在 _SIZE_MAP 中，会走 passthrough 分支，quality 不会被设置，所以只测有映射的
            for img_size, expected_quality in [("512px", "low"), ("1K", "medium"), ("2K", "high")]:
                output_path = tmp_path / f"output_{img_size}.png"
                request = ImageGenerationRequest(
                    prompt="test",
                    output_path=output_path,
                    aspect_ratio="9:16",
                    image_size=img_size,
                )
                await backend.generate(request)
                call_kwargs = mock_client.images.generate.call_args[1]
                assert call_kwargs["quality"] == expected_quality, f"size={img_size}"

    async def test_text_to_image_captures_usage(self, tmp_path: Path):
        """SDK 返回 usage 时，结果应携带 token 拆分字段。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(
                b64_data,
                usage={
                    "input_tokens": 500,
                    "output_tokens": 2200,
                    "input_tokens_details": {"text_tokens": 500, "image_tokens": 0},
                    # 不返回 output_tokens_details；img_out 应回退到顶层 output_tokens
                },
            )
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="usage capture",
                output_path=tmp_path / "out.png",
                aspect_ratio="9:16",
                image_size="1K",
            )
            result = await backend.generate(request)

        assert result.text_input_tokens == 500
        assert result.image_input_tokens == 0
        assert result.image_output_tokens == 2200  # 顶层 fallback
        assert result.text_output_tokens is None

    async def test_text_to_image_no_usage_returns_none(self, tmp_path: Path):
        """SDK 不返回 usage 时，4 个 token 字段全部为 None。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="no usage",
                output_path=tmp_path / "out.png",
                aspect_ratio="9:16",
                image_size="1K",
            )
            result = await backend.generate(request)

        assert result.image_input_tokens is None
        assert result.image_output_tokens is None
        assert result.text_input_tokens is None
        assert result.text_output_tokens is None

    async def test_image_to_image_captures_image_input_tokens(self, tmp_path: Path):
        """I2I 路径应能解析 input_tokens_details.image_tokens（参考图 token）。"""
        b64_data = base64.b64encode(b"edited").decode()
        mock_client = AsyncMock()
        mock_client.images.edit = AsyncMock(
            return_value=_make_mock_image_response(
                b64_data,
                usage={
                    "input_tokens": 12000,
                    "output_tokens": 2200,
                    "input_tokens_details": {"text_tokens": 200, "image_tokens": 11800},
                    "output_tokens_details": {"image_tokens": 2200, "text_tokens": 0},
                },
            )
        )

        ref_path = tmp_path / "ref.png"
        ref_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="edit",
                output_path=tmp_path / "out.png",
                reference_images=[ReferenceImage(path=str(ref_path))],
            )
            result = await backend.generate(request)

        assert result.image_input_tokens == 11800
        assert result.text_input_tokens == 200
        assert result.image_output_tokens == 2200
        assert result.text_output_tokens == 0
