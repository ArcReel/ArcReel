"""AgnesImageBackend: respx captures outbound single-step synchronous OpenAI-compatible requests."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.backends.providers import PROVIDER_AGNES
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, only_request, request_json

_ENDPOINT = "https://apihub.agnes-ai.com/v1/images/generations"


def _img_response(url: str = "https://x/out.png") -> httpx.Response:
    return httpx.Response(200, json={"created": 1, "data": [{"url": url}]})


def _b64_response(b64: str) -> httpx.Response:
    return httpx.Response(200, json={"created": 1, "data": [{"b64_json": b64}]})


def _make_ref(tmp_path: Path, name: str) -> ReferenceImage:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\nfake")
    return ReferenceImage(path=str(p))


@contextmanager
def _generate_route(response: httpx.Response, download: AsyncMock | None = None) -> Generator[respx.Route]:
    """Intercept generation POST/optional download, yielding routes for real-request assertions."""
    with capture_http() as router:
        route = router.post(_ENDPOINT).mock(return_value=response)
        if download is None:
            yield route
        else:
            with patch("lib.backends.image_backends.agnes.download_image_to_path", download):
                yield route


class TestCapabilities:
    def test_t2i_and_i2i(self):
        from lib.backends.image_backends.agnes import AgnesImageBackend

        b = AgnesImageBackend(api_key="sk", model="agnes-image-2.1-flash")
        assert b.name == PROVIDER_AGNES
        assert b.model == "agnes-image-2.1-flash"
        assert b.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    def test_declares_no_reference_image_limit(self):
        from lib.backends.image_backends.agnes import AgnesImageBackend

        # Channel forwards every reference without count truncation, as i2i tests show; declare no cap.
        assert asyncio.run(AgnesImageBackend(api_key="sk").reference_image_limit()) is None

    def test_default_model_when_unset(self):
        from lib.backends.image_backends.agnes import AgnesImageBackend

        assert AgnesImageBackend(api_key="sk").model == "agnes-image-2.1-flash"

    def test_registered_in_factory(self):
        from lib.backends.image_backends import create_backend, get_registered_backends
        from lib.backends.image_backends.agnes import AgnesImageBackend

        assert PROVIDER_AGNES in get_registered_backends()
        assert isinstance(create_backend(PROVIDER_AGNES, api_key="sk"), AgnesImageBackend)


class TestTextToImage:
    async def test_t2i_request_build(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk", model="agnes-image-2.1-flash", base_url="https://apihub.agnes-ai.com")
            result = await b.generate(ImageGenerationRequest(prompt="a fox", output_path=tmp_path / "o.png"))

        request = only_request(route)
        body = request_json(request)
        assert body["model"] == "agnes-image-2.1-flash"
        assert body["prompt"] == "a fox"
        assert body["n"] == 1
        # Upstream litellm rejects response_format with UnsupportedParamsError; never send it.
        assert "response_format" not in body
        assert "image" not in body
        # Compute default 9:16 exactly, capped at 2048 per edge.
        assert body["size"] == "1152x2048"
        # Derive /v1 + /images/generations from base hosts.
        assert str(request.url) == _ENDPOINT
        assert request.headers["Authorization"] == "Bearer sk"
        assert result.provider == PROVIDER_AGNES
        assert result.model == "agnes-image-2.1-flash"
        assert result.image_uri == "https://x/out.png"
        download.assert_called_once()

    async def test_default_endpoint(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

        assert str(only_request(route).url) == _ENDPOINT

    async def test_v25_request_build_uses_size_tier_and_ratio(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.agnes import AgnesTierImageBackend

            backend = AgnesTierImageBackend(api_key="sk", model="agnes-image-2.5-flash")
            result = await backend.generate(
                ImageGenerationRequest(
                    prompt="a fox",
                    output_path=tmp_path / "o.png",
                    aspect_ratio="16:9",
                    image_size="2K",
                )
            )

        body = request_json(only_request(route))
        assert body == {
            "model": "agnes-image-2.5-flash",
            "prompt": "a fox",
            "n": 1,
            "size": "2K",
            "ratio": "16:9",
        }
        assert result.model == "agnes-image-2.5-flash"
        assert result.usage is not None
        assert result.usage.dimensions == {"resolution": "2K"}
        assert result.usage.quantities == {"output_images": 1, "input_images": 0}

    def test_v21_bills_by_short_edge_tier_of_sent_size(self):
        from lib.backends.image_backends.agnes import AgnesImageBackend
        from lib.backends.image_backends.base import ImageRequestFacts

        def resolution(image_size: str | None) -> object:
            facts = ImageRequestFacts(model="agnes-image-2.1-flash", aspect_ratio="9:16", image_size=image_size)
            return AgnesImageBackend.estimate_usage(facts).dimensions["resolution"]

        assert resolution("1K") == "1K"
        # 4K requests cap at edge 2048; actual dimensions fall in 2K.
        assert resolution("4K") == "2K"
        assert resolution(None) == "2K"

    async def test_v25_default_size_is_1k(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.agnes import AgnesTierImageBackend

            backend = AgnesTierImageBackend(api_key="sk", model="agnes-image-2.5-flash")
            await backend.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

        body = request_json(only_request(route))
        assert body["size"] == "1K"
        assert body["ratio"] == "9:16"


class TestDimensions:
    async def _size(self, tmp_path: Path, **req_kwargs) -> str:
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk", model="agnes-image-2.1-flash")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png", **req_kwargs))
        return request_json(only_request(route))["size"]

    async def test_landscape_picks_wide(self, tmp_path: Path):
        assert await self._size(tmp_path, aspect_ratio="16:9") == "2048x1152"

    async def test_square(self, tmp_path: Path):
        assert await self._size(tmp_path, aspect_ratio="1:1") == "1440x1440"

    async def test_explicit_1k_tier(self, tmp_path: Path):
        assert await self._size(tmp_path, aspect_ratio="9:16", image_size="1K") == "1008x1792"

    async def test_custom_pixel_strips_embedded_ratio(self, tmp_path: Path):
        # Custom 1920*1080 contributes short edge min=1080 only; project aspect_ratio=9:16 still determines shape.
        size = await self._size(tmp_path, aspect_ratio="9:16", image_size="1920*1080")
        w, h = (int(v) for v in size.split("x"))
        assert w * 16 == h * 9
        assert w < h

    @pytest.mark.parametrize("aspect", ["9:16", "16:9", "1:1", "3:4", "4:3", "2:3", "3:2"])
    async def test_dims_multiple_of_8(self, tmp_path: Path, aspect: str):
        size = await self._size(tmp_path, aspect_ratio=aspect)
        w, h = (int(v) for v in size.split("x"))
        assert w % 8 == 0
        assert h % 8 == 0
        assert max(w, h) <= 2048


class TestImageToImage:
    async def test_i2i_reference_images_as_data_uri_list(self, tmp_path: Path):
        download = AsyncMock()
        refs = [_make_ref(tmp_path, f"r{i}.png") for i in range(2)]
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.agnes import AgnesTierImageBackend

            b = AgnesTierImageBackend(api_key="sk")
            await b.generate(
                ImageGenerationRequest(prompt="hero", output_path=tmp_path / "o.png", reference_images=refs)
            )

        body = request_json(only_request(route))
        images = body["image"]
        assert isinstance(images, list)
        assert len(images) == 2
        assert all(item.startswith("data:image/png;base64,") for item in images)
        # I2I explicitly sends the new size-tier contract.
        assert body["size"] == "1K"
        assert body["ratio"] == "9:16"

    async def test_legacy_i2i_keeps_pixel_size_and_data_uri_list(self, tmp_path: Path):
        download = AsyncMock()
        refs = [_make_ref(tmp_path, f"r{i}.png") for i in range(2)]
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.agnes import AgnesImageBackend

            backend = AgnesImageBackend(api_key="sk", model="agnes-image-2.1-flash")
            await backend.generate(
                ImageGenerationRequest(prompt="hero", output_path=tmp_path / "o.png", reference_images=refs)
            )

        body = request_json(only_request(route))
        assert body["size"] == "1152x2048"
        assert "ratio" not in body
        assert all(item.startswith("data:image/png;base64,") for item in body["image"])

    async def test_missing_ref_raises_unreadable(self, tmp_path: Path):
        from lib.backends.image_backends.agnes import AgnesImageBackend

        b = AgnesImageBackend(api_key="sk")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(
                ImageGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.png",
                    reference_images=[ReferenceImage(path=str(tmp_path / "nope.png"))],
                )
            )
        assert ei.value.code == "image_reference_images_unreadable"

    async def test_empty_ref_path_treated_as_missing(self, tmp_path: Path):
        from lib.backends.image_backends.agnes import AgnesImageBackend

        b = AgnesImageBackend(api_key="sk")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(
                ImageGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.png", reference_images=[ReferenceImage(path="")]
                )
            )
        assert ei.value.code == "image_reference_images_unreadable"


class TestResponseHandling:
    async def test_base64_response_decoded_and_saved(self, tmp_path: Path):
        raw = b"\x89PNG\r\nhello-bytes"
        b64 = base64.b64encode(raw).decode("ascii")
        out = tmp_path / "o.png"
        with _generate_route(_b64_response(b64)):
            from lib.backends.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            result = await b.generate(ImageGenerationRequest(prompt="x", output_path=out))

        assert out.read_bytes() == raw
        # Base64 paths have no remote URLs.
        assert result.image_uri is None

    async def test_base64_data_uri_prefix_stripped(self, tmp_path: Path):
        raw = b"PNGDATA"
        b64 = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        out = tmp_path / "o.png"
        with _generate_route(_b64_response(b64)):
            from lib.backends.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=out))

        assert out.read_bytes() == raw

    async def test_empty_data_raises_runtime(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(httpx.Response(200, json={"created": 1, "data": []}), download):
            from lib.backends.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            with pytest.raises(RuntimeError):
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

    async def test_url_download_failure_falls_back_to_b64(self, tmp_path: Path):
        # Failed URL downloads with same-response b64_json fall back to disk, preserving paid successful generation.
        raw = b"\x89PNG\r\nfallback"
        b64 = base64.b64encode(raw).decode("ascii")
        response = httpx.Response(200, json={"created": 1, "data": [{"url": "https://x/out.png", "b64_json": b64}]})
        download = AsyncMock(side_effect=RuntimeError("download boom"))
        out = tmp_path / "o.png"
        with _generate_route(response, download):
            from lib.backends.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            result = await b.generate(ImageGenerationRequest(prompt="x", output_path=out))

        assert out.read_bytes() == raw
        # Base64 fallback has no remote URLs.
        assert result.image_uri is None
        download.assert_called()

    async def test_url_download_failure_without_b64_still_raises(self, tmp_path: Path):
        # URL-only failed downloads without b64 fallback propagate, never silently swallow failures.
        download = AsyncMock(side_effect=RuntimeError("download boom"))
        with _generate_route(_img_response(), download):
            from lib.backends.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            with pytest.raises(RuntimeError):
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

    async def test_missing_url_b64_log_redacts_body(self, tmp_path: Path, caplog):
        # Missing url/b64 logs keys/data counts only, never sensitive values.
        response = httpx.Response(
            200,
            json={
                "created": 1,
                "data": [{"prompt": "secret-prompt"}],
                "signed_url": "https://x/secret?sig=abc",
            },
        )
        download = AsyncMock()
        with _generate_route(response, download), caplog.at_level("ERROR"):
            from lib.backends.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            with pytest.raises(RuntimeError):
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

        log_text = caplog.text
        assert "secret-prompt" not in log_text
        assert "sig=abc" not in log_text
        # Log key names for diagnosis.
        assert "signed_url" in log_text


class TestHttpErrors:
    async def test_400_surfaces_httpstatuserror_single_call(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(httpx.Response(400, text="boom"), download) as route:
            from lib.backends.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        assert ei.value.response.status_code == 400
        assert route.call_count == 1
        download.assert_not_called()


class TestRetryScope:
    async def test_download_failure_does_not_retrigger_generation(self, tmp_path: Path):
        # Transient download failures retry download only, never nonidempotent generation POST, avoiding duplicate images/billing.
        from lib.backends.backend_runtime import VIDEO_POLL_MAX_CONSECUTIVE_FAILURES

        download = AsyncMock(side_effect=httpx.ConnectError("conn reset"))
        with bounded_poll_clock(), _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            # Exhausted shared budgets raise RuntimeError retaining the last original error.
            with pytest.raises(RuntimeError, match="conn reset"):
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))
        # Generation POST occurs once, billing once; all retries belong to downloads.
        assert route.call_count == 1
        assert download.call_count == VIDEO_POLL_MAX_CONSECUTIVE_FAILURES


class TestPricing:
    def test_legacy_image_by_resolution_usd(self):
        from tests.catalog_support import builtin_price

        amount, currency = builtin_price(
            PROVIDER_AGNES, "agnes-image-2.1-flash", "image", {"output_images": 1}, {"resolution": "1K"}
        )
        assert amount == pytest.approx(0.010)
        assert currency == "USD"
