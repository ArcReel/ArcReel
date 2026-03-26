"""GrokImageBackend 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ReferenceImage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def _patch_xai_sdk():
    """Patch xai_sdk 以免依赖真实 SDK。"""
    mock_sdk = MagicMock()
    mock_client_instance = MagicMock()
    mock_sdk.AsyncClient.return_value = mock_client_instance
    with patch.dict("sys.modules", {"xai_sdk": mock_sdk}):
        yield mock_sdk, mock_client_instance


@pytest.fixture()
def backend(_patch_xai_sdk):
    from lib.image_backends.grok import GrokImageBackend
    return GrokImageBackend(api_key="fake-xai-key")


@pytest.fixture()
def backend_pro(_patch_xai_sdk):
    from lib.image_backends.grok import GrokImageBackend
    return GrokImageBackend(api_key="fake-xai-key", model="grok-imagine-image-pro")


# ---------------------------------------------------------------------------
# 属性测试
# ---------------------------------------------------------------------------

class TestProperties:
    def test_name(self, backend):
        assert backend.name == "grok"

    def test_model_default(self, backend):
        assert backend.model == "grok-imagine-image"

    def test_model_custom(self, backend_pro):
        assert backend_pro.model == "grok-imagine-image-pro"

    def test_capabilities(self, backend):
        assert backend.capabilities == {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }


# ---------------------------------------------------------------------------
# 构造函数测试
# ---------------------------------------------------------------------------

class TestInit:
    def test_missing_api_key_raises(self, _patch_xai_sdk):
        from lib.image_backends.grok import GrokImageBackend
        with pytest.raises(ValueError, match="XAI_API_KEY"):
            GrokImageBackend()

    def test_empty_api_key_raises(self, _patch_xai_sdk):
        from lib.image_backends.grok import GrokImageBackend
        with pytest.raises(ValueError, match="XAI_API_KEY"):
            GrokImageBackend(api_key="")


# ---------------------------------------------------------------------------
# generate() T2I 测试
# ---------------------------------------------------------------------------

class TestGenerateT2I:
    async def test_t2i_calls_image_sample(self, backend, tmp_path):
        """T2I 调用 client.image.sample 并下载结果。"""
        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/generated.png"
        backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        with patch("lib.image_backends.grok.httpx.AsyncClient") as MockHttpClient:
            mock_http = AsyncMock()
            MockHttpClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockHttpClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.content = fake_image_bytes
            mock_resp.raise_for_status = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_resp)

            request = ImageGenerationRequest(
                prompt="A beautiful sunset",
                output_path=output,
                aspect_ratio="16:9",
                image_size="2K",
            )
            result = await backend.generate(request)

        # 验证 SDK 调用参数
        backend._client.image.sample.assert_awaited_once_with(
            prompt="A beautiful sunset",
            model="grok-imagine-image",
            aspect_ratio="16:9",
            resolution="2k",
        )
        assert result.image_path == output
        assert result.provider == "grok"
        assert result.model == "grok-imagine-image"
        assert result.image_uri == "https://example.com/generated.png"
        # 验证文件已写入
        assert output.read_bytes() == fake_image_bytes


# ---------------------------------------------------------------------------
# generate() I2I 测试
# ---------------------------------------------------------------------------

class TestGenerateI2I:
    async def test_i2i_sends_data_uri(self, backend, tmp_path):
        """I2I 将参考图转为 data URI 传给 image_url。"""
        # 创建假参考图
        ref_image = tmp_path / "ref.png"
        ref_image.write_bytes(b"\x89PNG\r\n\x1a\nfake_png_data")

        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/edited.png"
        backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

        with patch("lib.image_backends.grok.httpx.AsyncClient") as MockHttpClient:
            mock_http = AsyncMock()
            MockHttpClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockHttpClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.content = fake_image_bytes
            mock_resp.raise_for_status = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_resp)

            request = ImageGenerationRequest(
                prompt="Make it darker",
                output_path=output,
                reference_images=[ReferenceImage(path=str(ref_image), label="base")],
            )
            result = await backend.generate(request)

        # 验证 image_url 参数包含 data URI
        call_kwargs = backend._client.image.sample.call_args.kwargs
        assert "image_url" in call_kwargs
        assert call_kwargs["image_url"].startswith("data:image/png;base64,")
        assert result.provider == "grok"

    async def test_i2i_skips_missing_ref(self, backend, tmp_path):
        """参考图不存在时退化为 T2I。"""
        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/generated.png"
        backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n"

        with patch("lib.image_backends.grok.httpx.AsyncClient") as MockHttpClient:
            mock_http = AsyncMock()
            MockHttpClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockHttpClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.content = fake_image_bytes
            mock_resp.raise_for_status = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_resp)

            request = ImageGenerationRequest(
                prompt="A cat",
                output_path=output,
                reference_images=[ReferenceImage(path="/nonexistent/ref.png")],
            )
            await backend.generate(request)

        call_kwargs = backend._client.image.sample.call_args.kwargs
        assert "image_url" not in call_kwargs


# ---------------------------------------------------------------------------
# 审核测试
# ---------------------------------------------------------------------------

class TestModeration:
    async def test_moderation_failure_raises(self, backend, tmp_path):
        """respect_moderation=False 时抛出 RuntimeError。"""
        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = False
        backend._client.image.sample = AsyncMock(return_value=mock_response)

        request = ImageGenerationRequest(
            prompt="Something problematic",
            output_path=output,
        )
        with pytest.raises(RuntimeError, match="内容审核"):
            await backend.generate(request)


# ---------------------------------------------------------------------------
# resolution 映射测试
# ---------------------------------------------------------------------------

class TestResolutionMapping:
    def test_map_image_size(self, _patch_xai_sdk):
        from lib.image_backends.grok import _map_image_size_to_resolution
        assert _map_image_size_to_resolution("1K") == "1k"
        assert _map_image_size_to_resolution("2K") == "2k"
        assert _map_image_size_to_resolution("unknown") == "1k"
