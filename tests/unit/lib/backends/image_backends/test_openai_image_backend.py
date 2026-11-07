"""OpenAIImageBackend unit tests."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lib.backends.image_backends import ImageCapabilityError
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.backends.providers import PROVIDER_OPENAI
from tests.fakes import captured_openai_clients
from tests.http_capture import capture_http, only_request


def _make_mock_image_response(
    b64_data: str | None = "aW1hZ2VfZGF0YQ==",
    url: str | None = None,
    usage=None,
):
    """Build mocked ImagesResponse.

    - usage=None deliberately omits the attribute, simulating SDKs without usage;
    - Dicts {"input_tokens": ..., "output_tokens": ..., "input_tokens_details": {...}, "output_tokens_details": {...}}
      build nested MagicMocks from keys.
    """
    datum = MagicMock()
    datum.b64_json = b64_data
    datum.url = url

    # Restrict attributes through spec, preventing MagicMock creation of unknown attributes.
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
        with captured_openai_clients():
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            assert backend.name == PROVIDER_OPENAI
            assert backend.model == "gpt-image-2"

    def test_custom_model(self):
        with captured_openai_clients():
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key", model="custom-image-model")
            assert backend.model == "custom-image-model"

    def test_capabilities(self):
        with captured_openai_clients():
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            assert ImageCapability.TEXT_TO_IMAGE in backend.capabilities
            assert ImageCapability.IMAGE_TO_IMAGE in backend.capabilities

    async def test_text_to_image(self, tmp_path: Path):
        """T2I calls images.generate()."""
        b64_data = base64.b64encode(b"fake-png-data").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

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
        assert call_kwargs["size"] == "1008x1792"  # Exact 9:16 with 1K short edge 1024.
        assert call_kwargs["quality"] == "medium"  # 1K
        # GPT Image rejects response_format on strict-compatible gateways with 400; never send it.
        assert "response_format" not in call_kwargs

    async def test_sends_every_reference_above_the_channel_limit(self, tmp_path: Path):
        """Forward all references admitted by capability overrides."""
        mock_client = AsyncMock()
        mock_client.images.edit = AsyncMock(return_value=_make_mock_image_response())

        refs = []
        for index in range(20):
            ref_path = tmp_path / f"ref{index}.png"
            ref_path.write_bytes(b"\x89PNG\r\n\x1a\n")
            refs.append(ReferenceImage(path=str(ref_path)))

        output_path = tmp_path / "output.png"
        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            from lib.backends.image_capabilities import gate_image_request
            from lib.custom_provider.backends import CustomImageBackend

            backend = CustomImageBackend(
                provider_id="custom-1",
                delegate=backend,
                model=backend.model,
                reference_limit_override=20,
            )
            await gate_image_request(backend, provider_id="custom-1", reference_count=20)
            result = await backend.generate(
                ImageGenerationRequest(
                    prompt="Edit these",
                    output_path=output_path,
                    reference_images=refs,
                )
            )

        assert result.image_path == output_path
        assert output_path.read_bytes() == b"image_data"
        assert len(mock_client.images.edit.call_args[1]["image"]) == 20

    async def test_image_to_image(self, tmp_path: Path):
        """I2I calls images.edit()."""
        b64_data = base64.b64encode(b"edited-image").decode()
        mock_client = AsyncMock()
        mock_client.images.edit = AsyncMock(return_value=_make_mock_image_response(b64_data))

        ref_path = tmp_path / "ref.png"
        ref_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

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
        # I2I forwards size like T2I: default 9:16 + image_size=None → short-edge 720 fallback → 720x1280.
        # Previously images.edit omitted size entirely, letting upstream defaults override ratios.
        assert edit_kwargs["size"] == "720x1280"

    async def test_empty_data_raises(self, tmp_path: Path):
        """Empty OpenAI data arrays raise clear RuntimeError, not IndexError."""
        empty_response = MagicMock(spec=["data", "usage"])
        empty_response.data = []
        empty_response.usage = None

        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=empty_response)

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="A beautiful sunset",
                output_path=tmp_path / "output.png",
                aspect_ratio="9:16",
                image_size="1K",
            )

            with pytest.raises(RuntimeError, match="data 为空"):
                await backend.generate(request)

    async def test_text_to_image_url_fallback(self, tmp_path: Path):
        """URL-only gateway results use httpx downloads."""
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(b64_data=None, url="https://gateway/img.png")
        )

        downloaded = b"downloaded-from-gateway"

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            output_path = tmp_path / "output.png"
            request = ImageGenerationRequest(
                prompt="A beautiful sunset",
                output_path=output_path,
                aspect_ratio="9:16",
                image_size="1K",
            )

            with capture_http() as router:
                download = router.get("https://gateway/img.png").mock(
                    return_value=httpx.Response(200, content=downloaded)
                )
                result = await backend.generate(request)

            assert str(only_request(download).url) == "https://gateway/img.png"

        assert result.image_path == output_path
        assert output_path.read_bytes() == downloaded

    async def test_size_mapping(self, tmp_path: Path):
        """aspect_ratio precisely determines size, with 1K short edge 1024 and zero ratio error."""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")

            # Iterate aspect_ratio with image_size="1K", short edge 1024, preserving exact ratios.
            for aspect, expected_size in [("16:9", "1792x1008"), ("1:1", "1024x1024"), ("9:16", "1008x1792")]:
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
        """Map image_size to standard quality tokens."""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")

            # quality still follows mapped image_size tiers; iterate mapped tiers.
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
        """SDK usage results include token-breakdown fields."""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(
                b64_data,
                usage={
                    "input_tokens": 500,
                    "output_tokens": 2200,
                    "input_tokens_details": {"text_tokens": 500, "image_tokens": 0},
                    # Missing output_tokens_details makes img_out fall back to top-level output_tokens.
                },
            )
        )

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

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
        assert result.image_output_tokens == 2200  # Top-level fallback
        assert result.text_output_tokens is None
        # Report token usage with selection dimensions from actual size/quality requests.
        assert result.usage is not None
        assert result.usage.quantities == {
            "uncached_input_tokens": 500,
            "uncached_image_input_tokens": 0,
            "image_output_tokens": 2200,
        }
        assert result.usage.dimensions == {"resolution": "1008x1792", "quality": "medium"}

    async def test_text_to_image_no_usage_returns_none(self, tmp_path: Path):
        """Missing SDK usage leaves all four token fields None."""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

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
        # Without usage report counts only; never estimate billing tokens from dimensions/quality here.
        assert result.usage is not None
        assert result.usage.quantities == {"output_images": 1}

    async def test_partial_usage_no_input_details_falls_back(self, tmp_path: Path):
        """Top-level input_tokens without input_tokens_details clears all four fields, selecting static cost fallback.

        Compatible gateways/trimmed responses missing input breakdowns would systematically
        undercount input costs if token pricing remained active.
        """
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(
                b64_data,
                usage={
                    "input_tokens": 500,  # Top-level present.
                    "output_tokens": 2200,
                    "input_tokens_details": None,  # Breakdown absent.
                },
            )
        )

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="partial usage",
                output_path=tmp_path / "out.png",
                aspect_ratio="9:16",
                image_size="1K",
            )
            result = await backend.generate(request)

        assert result.image_input_tokens is None
        assert result.image_output_tokens is None
        assert result.text_input_tokens is None
        assert result.text_output_tokens is None

    async def test_input_details_empty_inner_fields_falls_back(self, tmp_path: Path):
        """Existing input_tokens_details with image_tokens/text_tokens both None counts as no breakdown, using fallback."""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(
                b64_data,
                usage={
                    "input_tokens": 500,
                    "output_tokens": 2200,
                    "input_tokens_details": {"image_tokens": None, "text_tokens": None},
                },
            )
        )

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="empty details",
                output_path=tmp_path / "out.png",
                aspect_ratio="9:16",
                image_size="1K",
            )
            result = await backend.generate(request)

        assert result.image_input_tokens is None
        assert result.text_input_tokens is None
        assert result.image_output_tokens is None
        assert result.text_output_tokens is None

    async def test_input_details_present_but_no_output_falls_back(self, tmp_path: Path):
        """Known input breakdown without any output information clears all four fields, avoiding input-only underbilling."""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(
                b64_data,
                usage={
                    # Neither top-level output_tokens nor output_tokens_details exists.
                    "input_tokens": 500,
                    "input_tokens_details": {"text_tokens": 500, "image_tokens": 0},
                },
            )
        )

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="no output info",
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
        """I2I parses reference tokens from input_tokens_details.image_tokens."""
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

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

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


class TestModeCapabilities:
    def test_default_mode_is_both(self):
        with patch("lib.backends.image_backends.openai.create_openai_client"):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            b = OpenAIImageBackend(api_key="x", model="m")
            assert ImageCapability.TEXT_TO_IMAGE in b.capabilities
            assert ImageCapability.IMAGE_TO_IMAGE in b.capabilities

    def test_generations_only_mode(self):
        with patch("lib.backends.image_backends.openai.create_openai_client"):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            b = OpenAIImageBackend(api_key="x", model="m", mode="generations_only")
            assert b.capabilities == {ImageCapability.TEXT_TO_IMAGE}

    def test_edits_only_mode(self):
        with patch("lib.backends.image_backends.openai.create_openai_client"):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            b = OpenAIImageBackend(api_key="x", model="m", mode="edits_only")
            assert b.capabilities == {ImageCapability.IMAGE_TO_IMAGE}


class TestModeGating:
    @pytest.mark.asyncio
    async def test_generations_only_with_refs_raises(self, tmp_path):
        ref = tmp_path / "r.png"
        ref.write_bytes(b"\x89PNG")
        with patch("lib.backends.image_backends.openai.create_openai_client"):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            b = OpenAIImageBackend(api_key="x", model="m", mode="generations_only")
            req = ImageGenerationRequest(
                prompt="p",
                output_path=tmp_path / "o.png",
                reference_images=[ReferenceImage(path=str(ref))],
            )
            with pytest.raises(ImageCapabilityError) as excinfo:
                await b.generate(req)
            assert excinfo.value.code == "image_endpoint_mismatch_no_i2i"
            assert excinfo.value.params == {"model": "m"}

    @pytest.mark.asyncio
    async def test_edits_only_without_refs_raises(self, tmp_path):
        with patch("lib.backends.image_backends.openai.create_openai_client"):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            b = OpenAIImageBackend(api_key="x", model="m", mode="edits_only")
            req = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png")
            with pytest.raises(ImageCapabilityError) as excinfo:
                await b.generate(req)
            assert excinfo.value.code == "image_endpoint_mismatch_no_t2i"

    @pytest.mark.asyncio
    async def test_all_refs_failed_to_open_raises(self, tmp_path):
        """All unreadable references raise ImageCapabilityError, never T2I fallback."""
        with captured_openai_clients():
            from lib.backends.image_backends.openai import OpenAIImageBackend

            b = OpenAIImageBackend(api_key="x", model="m")  # Default mode="both".
            req = ImageGenerationRequest(
                prompt="p",
                output_path=tmp_path / "o.png",
                reference_images=[ReferenceImage(path="/nonexistent/ref.png")],
            )
            with pytest.raises(ImageCapabilityError) as excinfo:
                await b.generate(req)
            assert excinfo.value.code == "image_endpoint_mismatch_no_i2i"
            assert excinfo.value.params.get("model") == "m"
            assert excinfo.value.params.get("detail") == "all reference images failed to open"


def _image_entry(name: str, output_token_bases: dict[str, int] | None = None):
    """Test image entries carry quality-tier output-token estimation bases only, independent of factory snapshots."""
    from arcreel_catalog_core import CatalogEntry, ImageFacts

    return CatalogEntry(
        maker="openai",
        name=name,
        media_type="image",
        display_name=name,
        facts=ImageFacts(text_to_image=True, image_to_image=True, output_token_bases=output_token_bases or {}),
    )


#: Official calculator quality bases from catalog GPT Image 2/GPT Image 2.5.
_GPT_IMAGE_2_BASES = {"low": 16, "medium": 48, "high": 96}
_GPT_IMAGE_2_5_BASES = {"low": 16, "medium": 24, "high": 48, "xhigh": 64, "max": 96}


class TestOutputTokenEstimate:
    """Output-token estimates use entry quality bases with official formulas; omit estimates for unrecorded requested tiers."""

    @pytest.mark.parametrize(
        ("size", "quality", "tokens", "official_price"),
        [
            ("1024x1024", "low", 196, 0.006),
            ("1024x1536", "low", 158, 0.005),
            ("1024x1024", "medium", 1756, 0.053),
            ("1536x1024", "medium", 1372, 0.041),
            ("1024x1024", "high", 7024, 0.211),
            ("1024x1536", "high", 5488, 0.165),
        ],
    )
    def test_matches_official_table(self, size: str, quality: str, tokens: int, official_price: float) -> None:
        from lib.backends.image_backends.openai import estimate_output_tokens

        assert estimate_output_tokens(size, quality, _GPT_IMAGE_2_BASES) == tokens
        # Official tables convert $30/million output tokens to per-image prices, retaining three decimals.
        assert round(tokens * 30 / 1_000_000, 3) == official_price

    def test_quality_missing_from_bases_or_unparsable_size_has_no_estimate(self) -> None:
        from lib.backends.image_backends.openai import estimate_output_tokens

        assert estimate_output_tokens("1024x1024", "auto", _GPT_IMAGE_2_BASES) is None
        assert estimate_output_tokens("1024x1024", "medium", {}) is None
        assert estimate_output_tokens("1K", "medium", _GPT_IMAGE_2_BASES) is None

    def test_estimate_usage_uses_request_size_and_quality(self) -> None:
        from lib.backends.image_backends.base import ImageRequestFacts
        from lib.backends.image_backends.openai import OpenAIImageBackend

        usage = OpenAIImageBackend.estimate_usage(
            ImageRequestFacts(
                model="gpt-image-2",
                aspect_ratio="9:16",
                image_size="1K",
                reference_count=0,
                entry=_image_entry("gpt-image-2", _GPT_IMAGE_2_BASES),
            )
        )
        assert usage.dimensions == {"resolution": "1008x1792", "quality": "medium"}
        assert usage.quantities["image_output_tokens"] == 1234
        # Unknown prompt tokens count 0; T2I has no references, so image-input tokens also 0.
        assert usage.quantities["uncached_input_tokens"] == 0
        assert usage.quantities["uncached_image_input_tokens"] == 0

    @pytest.mark.parametrize(
        ("image_size", "aspect_ratio", "tokens"),
        [
            # 1K→medium: 2.5 base 24 versus gpt-image-2 base 48, about quarter tokens at equal size.
            ("1K", "1:1", 439),
            # 2K→high: 2.5 high base 48 at 1440x1440.
            ("2K", "1:1", 2_347),
        ],
    )
    def test_gpt_image_2_5_is_estimated_from_its_entry(self, image_size: str, aspect_ratio: str, tokens: int) -> None:
        from lib.backends.image_backends.base import ImageRequestFacts
        from lib.backends.image_backends.openai import OpenAIImageBackend

        usage = OpenAIImageBackend.estimate_usage(
            ImageRequestFacts(
                model="gpt-image-2.5",
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                entry=_image_entry("gpt-image-2.5", _GPT_IMAGE_2_5_BASES),
            )
        )
        assert usage.quantities["image_output_tokens"] == tokens

    def test_model_name_does_not_decide_whether_tokens_are_estimated(self) -> None:
        """Estimate from entries only: opaque API names bound to recorded bases work; gpt-image-2 names without recorded bases do not."""
        from lib.backends.image_backends.base import ImageRequestFacts
        from lib.backends.image_backends.openai import OpenAIImageBackend

        def output(model: str, entry) -> object:
            facts = ImageRequestFacts(model=model, aspect_ratio="1:1", image_size="1K", entry=entry)
            return OpenAIImageBackend.estimate_usage(facts).quantities.get("image_output_tokens")

        assert output("relay-image-xyz", _image_entry("gpt-image-2", _GPT_IMAGE_2_BASES)) == 1756
        assert output("gpt-image-2", _image_entry("gpt-image-2")) is None
        assert output("gpt-image-2", None) is None
        # Partially recorded quality sets omit requested medium, so do not estimate.
        assert output("gpt-image-2", _image_entry("gpt-image-2", {"low": 16})) is None

    def test_estimate_without_quality_or_with_references_leaves_tokens_unknown(self) -> None:
        from lib.backends.image_backends.base import ImageRequestFacts
        from lib.backends.image_backends.openai import OpenAIImageBackend

        entry = _image_entry("gpt-image-2", _GPT_IMAGE_2_BASES)
        no_quality = OpenAIImageBackend.estimate_usage(
            ImageRequestFacts(model="gpt-image-2", aspect_ratio="9:16", image_size=None, entry=entry)
        )
        assert "image_output_tokens" not in no_quality.quantities
        with_refs = OpenAIImageBackend.estimate_usage(
            ImageRequestFacts(
                model="gpt-image-2", aspect_ratio="9:16", image_size="1K", reference_count=None, entry=entry
            )
        )
        assert "uncached_image_input_tokens" not in with_refs.quantities
