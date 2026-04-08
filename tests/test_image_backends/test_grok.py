"""Unit tests for GrokImageBackend."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ReferenceImage


# Fixtures


@pytest.fixture()
def _patch_xai_sdk():
    """Patch create_grok_client to avoid depending on the real SDK."""
    mock_client_instance = MagicMock()
    with patch("lib.image_backends.grok.create_grok_client", return_value=mock_client_instance):
        yield mock_client_instance


@pytest.fixture()
def backend(_patch_xai_sdk):
    from lib.image_backends.grok import GrokImageBackend

    return GrokImageBackend(api_key="fake-xai-key")


@pytest.fixture()
def backend_pro(_patch_xai_sdk):
    from lib.image_backends.grok import GrokImageBackend

    return GrokImageBackend(api_key="fake-xai-key", model="grok-imagine-image-pro")


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


class TestInit:
    def test_missing_api_key_raises(self):
        with patch("lib.image_backends.grok.create_grok_client", side_effect=ValueError("XAI_API_KEY not set")):
            from lib.image_backends.grok import GrokImageBackend

            with pytest.raises(ValueError, match="XAI_API_KEY"):
                GrokImageBackend()

    def test_empty_api_key_raises(self):
        with patch("lib.image_backends.grok.create_grok_client", side_effect=ValueError("XAI_API_KEY not set")):
            from lib.image_backends.grok import GrokImageBackend

            with pytest.raises(ValueError, match="XAI_API_KEY"):
                GrokImageBackend(api_key="")


# generate() T2I tests


class TestGenerateT2I:
    async def test_t2i_calls_image_sample(self, backend, tmp_path):
        """T2I calls client.image.sample and downloads the result."""
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

        # Verify SDK call arguments
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

        assert output.read_bytes() == fake_image_bytes


# generate() I2I tests


class TestGenerateI2I:
    async def test_i2i_sends_image_urls(self, backend, tmp_path):
        """I2I converts reference images to data URIs and passes them via image_urls."""
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

        call_kwargs = backend._client.image.sample.call_args.kwargs
        assert "image_urls" in call_kwargs
        assert "image_url" not in call_kwargs
        assert len(call_kwargs["image_urls"]) == 1
        assert call_kwargs["image_urls"][0].startswith("data:image/png;base64,")
        assert result.provider == "grok"

    async def test_i2i_multiple_refs(self, backend, tmp_path):
        """Multiple reference images are all passed via image_urls."""
        ref1 = tmp_path / "ref1.png"
        ref1.write_bytes(b"\x89PNG\r\n\x1a\nfake1")
        ref2 = tmp_path / "ref2.jpg"
        ref2.write_bytes(b"\xff\xd8\xff\xe0fake2")

        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/merged.png"
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
                prompt="Merge subjects",
                output_path=output,
                reference_images=[
                    ReferenceImage(path=str(ref1)),
                    ReferenceImage(path=str(ref2)),
                ],
            )
            await backend.generate(request)

        call_kwargs = backend._client.image.sample.call_args.kwargs
        assert len(call_kwargs["image_urls"]) == 2

    async def test_i2i_skips_missing_ref(self, backend, tmp_path):
        """Falls back to T2I when the reference image does not exist."""
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
        assert "image_urls" not in call_kwargs
        assert "image_url" not in call_kwargs


class TestModeration:
    async def test_moderation_failure_raises(self, backend, tmp_path):
        """Raises RuntimeError when respect_moderation=False."""
        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = False
        backend._client.image.sample = AsyncMock(return_value=mock_response)

        request = ImageGenerationRequest(
            prompt="Something problematic",
            output_path=output,
        )
        with pytest.raises(RuntimeError, match="content moderation"):
            await backend.generate(request)


# resolution mapping tests


class TestResolutionMapping:
    def test_map_image_size(self):
        from lib.image_backends.grok import _map_image_size_to_resolution

        assert _map_image_size_to_resolution("1K") == "1k"
        assert _map_image_size_to_resolution("2K") == "2k"
        assert _map_image_size_to_resolution("unknown") == "1k"


# aspect_ratio validation tests


class TestAspectRatioValidation:
    def test_supported_ratios_pass_through(self):
        from lib.image_backends.grok import _validate_aspect_ratio

        for ratio in ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2", "auto"):
            assert _validate_aspect_ratio(ratio) == ratio

    def test_unsupported_ratio_passed_through_with_warning(self):
        from lib.image_backends.grok import _validate_aspect_ratio

        # Unsupported ratios are passed through to the API without mapping
        assert _validate_aspect_ratio("5:4") == "5:4"
