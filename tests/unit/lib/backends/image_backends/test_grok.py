"""GrokImageBackend unit tests."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from lib.backends.image_backends.base import ImageCapability, ImageGenerationRequest, ReferenceImage
from tests.fakes import blocking_file_read_gate
from tests.http_capture import capture_http

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@contextmanager
def _image_download(url: str, content: bytes) -> Generator[respx.Route]:
    """Base-layer output downloads use real httpx bytes intercepted by transport-level respx."""
    with capture_http() as router:
        yield router.get(url).mock(return_value=httpx.Response(200, content=content))


@pytest.fixture
def patch_xai_sdk():
    """Patch create_grok_client to avoid real SDK dependencies."""
    mock_client_instance = MagicMock()
    with patch("lib.backends.image_backends.grok.create_grok_client", return_value=mock_client_instance):
        yield mock_client_instance


@pytest.fixture
def grok_backend(patch_xai_sdk):
    from lib.backends.image_backends.grok import GrokImageBackend

    return GrokImageBackend(api_key="fake-xai-key")


@pytest.fixture
def backend_pro(patch_xai_sdk):
    from lib.backends.image_backends.grok import GrokImageBackend

    return GrokImageBackend(api_key="fake-xai-key", model="grok-imagine-image-pro")


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestProperties:
    def test_name(self, grok_backend):
        assert grok_backend.name == "grok"

    def test_model_default(self, grok_backend):
        assert grok_backend.model == "grok-imagine-image"

    def test_model_custom(self, backend_pro):
        assert backend_pro.model == "grok-imagine-image-pro"

    def test_capabilities(self, grok_backend):
        assert grok_backend.capabilities == {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @pytest.mark.parametrize("model", ["grok-imagine-image", "grok-imagine-image-pro"])
    def test_declares_grok_reference_image_limit(self, patch_xai_sdk, model):
        # Official xAI multi-image editing accepts at most 5 source images per request.
        from lib.backends.image_backends.grok import GrokImageBackend

        assert asyncio.run(GrokImageBackend(api_key="fake-xai-key", model=model).reference_image_limit()) == 5


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------


class TestInit:
    def test_missing_api_key_raises(self):
        with patch("lib.backends.image_backends.grok.create_grok_client", side_effect=ValueError("XAI_API_KEY 未设置")):
            from lib.backends.image_backends.grok import GrokImageBackend

            with pytest.raises(ValueError, match="XAI_API_KEY"):
                GrokImageBackend()

    def test_empty_api_key_raises(self):
        with patch("lib.backends.image_backends.grok.create_grok_client", side_effect=ValueError("XAI_API_KEY 未设置")):
            from lib.backends.image_backends.grok import GrokImageBackend

            with pytest.raises(ValueError, match="XAI_API_KEY"):
                GrokImageBackend(api_key="")


# ---------------------------------------------------------------------------
# generate() T2I tests
# ---------------------------------------------------------------------------


class TestGenerateT2I:
    async def test_t2i_calls_image_sample(self, grok_backend, tmp_path):
        """T2I calls client.image.sample and downloads outputs."""
        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/generated.png"
        grok_backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        with _image_download(mock_response.url, fake_image_bytes):
            request = ImageGenerationRequest(
                prompt="A beautiful sunset",
                output_path=output,
                aspect_ratio="16:9",
                image_size="2K",
            )
            result = await grok_backend.generate(request)

        # Verify SDK arguments: catalog 2K converts to accepted lowercase 2k.
        grok_backend._client.image.sample.assert_awaited_once_with(
            prompt="A beautiful sunset",
            model="grok-imagine-image",
            aspect_ratio="16:9",
            resolution="2k",
        )
        assert result.image_path == output
        assert result.provider == "grok"
        assert result.model == "grok-imagine-image"
        assert result.image_uri == "https://example.com/generated.png"
        # Verify files were written.
        assert output.read_bytes() == fake_image_bytes


# ---------------------------------------------------------------------------
# generate() I2I tests
# ---------------------------------------------------------------------------


class TestGenerateI2I:
    async def test_i2i_sends_image_urls(self, grok_backend, tmp_path):
        """I2I converts references into data-URI image_urls lists."""
        ref_image = tmp_path / "ref.png"
        ref_image.write_bytes(b"\x89PNG\r\n\x1a\nfake_png_data")

        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/edited.png"
        grok_backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

        with _image_download(mock_response.url, fake_image_bytes):
            request = ImageGenerationRequest(
                prompt="Make it darker",
                output_path=output,
                reference_images=[ReferenceImage(path=str(ref_image))],
            )
            result = await grok_backend.generate(request)

        call_kwargs = grok_backend._client.image.sample.call_args.kwargs
        assert "image_urls" in call_kwargs
        assert "image_url" not in call_kwargs
        assert len(call_kwargs["image_urls"]) == 1
        assert call_kwargs["image_urls"][0].startswith("data:image/png;base64,")
        assert result.provider == "grok"

    async def test_i2i_multiple_refs(self, grok_backend, tmp_path):
        """Forward all references through image_urls."""
        ref1 = tmp_path / "ref1.png"
        ref1.write_bytes(b"\x89PNG\r\n\x1a\nfake1")
        ref2 = tmp_path / "ref2.jpg"
        ref2.write_bytes(b"\xff\xd8\xff\xe0fake2")

        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/merged.png"
        grok_backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

        with _image_download(mock_response.url, fake_image_bytes):
            request = ImageGenerationRequest(
                prompt="Merge subjects",
                output_path=output,
                reference_images=[
                    ReferenceImage(path=str(ref1)),
                    ReferenceImage(path=str(ref2)),
                ],
            )
            await grok_backend.generate(request)

        call_kwargs = grok_backend._client.image.sample.call_args.kwargs
        assert len(call_kwargs["image_urls"]) == 2

    async def test_i2i_sends_every_reference_above_the_channel_limit(self, grok_backend, tmp_path):
        """Send every reference admitted by capability overrides."""
        refs = []
        for index in range(7):
            ref_path = tmp_path / f"ref{index}.png"
            ref_path.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([index]))
            refs.append(ReferenceImage(path=str(ref_path)))
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/merged.png"
        grok_backend._client.image.sample = AsyncMock(return_value=mock_response)

        from lib.backends.image_capabilities import gate_image_request
        from lib.custom_provider.backends import CustomImageBackend

        wrapped = CustomImageBackend(
            provider_id="custom-1",
            delegate=grok_backend,
            model=grok_backend.model,
            reference_limit_override=7,
        )
        await gate_image_request(wrapped, provider_id="custom-1", reference_count=7)
        with _image_download(mock_response.url, b"\x89PNG\r\n\x1a\n" + b"\x00" * 50):
            await wrapped.generate(
                ImageGenerationRequest(prompt="Merge", output_path=tmp_path / "out.png", reference_images=refs)
            )

        call_kwargs = grok_backend._client.image.sample.call_args.kwargs
        assert len(call_kwargs["image_urls"]) == 7

    async def test_i2i_reference_encoding_keeps_event_loop_running(self, grok_backend, tmp_path, monkeypatch):
        """Base64 reads whole images and must move to threads, preventing event-loop stalls."""
        ref_image = tmp_path / "ref.png"
        ref_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/edited.png"
        grok_backend._client.image.sample = AsyncMock(return_value=mock_response)

        with _image_download(mock_response.url, b"\x89PNG\r\n\x1a\n" + b"\x00" * 50):
            request = ImageGenerationRequest(
                prompt="Make it darker",
                output_path=output,
                reference_images=[ReferenceImage(path=str(ref_image))],
            )
            with blocking_file_read_gate(monkeypatch, ref_image) as gate:
                task = asyncio.create_task(grok_backend.generate(request))
                await gate.wait_until_read_started()
                gate.release()
                await task
                gate.assert_read_was_offloaded()

        call_kwargs = grok_backend._client.image.sample.call_args.kwargs
        assert call_kwargs["image_urls"][0].startswith("data:image/png;base64,")

    async def test_i2i_skips_missing_ref(self, grok_backend, tmp_path):
        """Absent references fall back to T2I."""
        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/generated.png"
        grok_backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n"

        with _image_download(mock_response.url, fake_image_bytes):
            request = ImageGenerationRequest(
                prompt="A cat",
                output_path=output,
                reference_images=[ReferenceImage(path="/nonexistent/ref.png")],
            )
            await grok_backend.generate(request)

        call_kwargs = grok_backend._client.image.sample.call_args.kwargs
        assert "image_urls" not in call_kwargs
        assert "image_url" not in call_kwargs


# ---------------------------------------------------------------------------
# Moderation tests
# ---------------------------------------------------------------------------


class TestModeration:
    async def test_moderation_failure_raises(self, grok_backend, tmp_path):
        """respect_moderation=False raises RuntimeError."""
        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = False
        grok_backend._client.image.sample = AsyncMock(return_value=mock_response)

        request = ImageGenerationRequest(
            prompt="Something problematic",
            output_path=output,
        )
        with pytest.raises(RuntimeError, match="内容审核"):
            await grok_backend.generate(request)


# ---------------------------------------------------------------------------
# Lowercase-resolution conversion tests
# ---------------------------------------------------------------------------


class TestResolutionLowercase:
    @pytest.mark.asyncio
    async def test_image_size_none_omits_resolution_kwarg(self, grok_backend, tmp_path):
        captured: dict = {}

        async def fake_sample(**kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop")

        grok_backend._client.image.sample = fake_sample
        request = ImageGenerationRequest(
            prompt="hi",
            output_path=tmp_path / "o.png",
            aspect_ratio="1:1",
            image_size=None,
        )
        with pytest.raises(RuntimeError):
            await grok_backend.generate(request)

        assert "resolution" not in captured

    @pytest.mark.asyncio
    async def test_image_size_lowercased_for_sdk(self, grok_backend, tmp_path):
        captured: dict = {}

        async def fake_sample(**kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop")

        grok_backend._client.image.sample = fake_sample
        request = ImageGenerationRequest(
            prompt="hi",
            output_path=tmp_path / "o.png",
            aspect_ratio="1:1",
            image_size="2K",
        )
        with pytest.raises(RuntimeError):
            await grok_backend.generate(request)

        assert captured["resolution"] == "2k"


# ---------------------------------------------------------------------------
# Aspect-ratio validation tests
# ---------------------------------------------------------------------------


class TestAspectRatioValidation:
    def test_supported_ratios_pass_through(self):
        from lib.backends.image_backends.grok import _validate_aspect_ratio

        for ratio in ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2", "auto"):
            assert _validate_aspect_ratio(ratio) == ratio

    def test_unsupported_ratio_passed_through_with_warning(self):
        from lib.backends.image_backends.grok import _validate_aspect_ratio

        # Unsupported ratios pass to APIs without mapping.
        assert _validate_aspect_ratio("5:4") == "5:4"
