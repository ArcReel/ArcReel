"""MiniMaxImageBackend synchronous single-step tests with substituted HTTP, no real requests."""

from __future__ import annotations

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
from lib.backends.providers import PROVIDER_MINIMAX
from tests.http_capture import capture_http, request_json


def _img_response(url: str = "https://x/out.png") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "trace-1",
            "data": {"image_urls": [url]},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        },
    )


def _b64_response(b64: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "trace-1",
            "data": {"image_base64": [b64]},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        },
    )


def _biz_error_response(status_code: int = 1004, msg: str = "invalid api key") -> httpx.Response:
    return httpx.Response(200, json={"base_resp": {"status_code": status_code, "status_msg": msg}})


def _make_ref(tmp_path: Path, name: str) -> ReferenceImage:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\nfake")
    return ReferenceImage(path=str(p))


def _error_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, text="Request Entity Too Large")


@contextmanager
def _generation_route(resp: httpx.Response, download: AsyncMock | None = None) -> Generator[respx.Route]:
    """Image endpoint transport-level respx capture.

    Assert actual endpoint/body/auth requests captured by routes. Optional ``download`` also
    takes over persistence for the independent non-base64 download stage.
    """
    with capture_http() as router:
        route = router.post(url__regex=r"https://[^/]+/v1/image_generation").mock(return_value=resp)
        if download is None:
            yield route
        else:
            with patch("lib.backends.image_backends.minimax.download_image_to_path", download):
                yield route


class TestCapabilities:
    def test_image_01_t2i_and_i2i(self):
        from lib.backends.image_backends.minimax import MiniMaxImageBackend

        b = MiniMaxImageBackend(api_key="sk", model="image-01")
        assert b.name == PROVIDER_MINIMAX
        assert b.model == "image-01"
        assert b.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    def test_default_model_when_unset(self):
        from lib.backends.image_backends.minimax import MiniMaxImageBackend

        assert MiniMaxImageBackend(api_key="sk").model == "image-01"

    def test_registered_in_factory(self):
        from lib.backends.image_backends import create_backend, get_registered_backends
        from lib.backends.image_backends.minimax import MiniMaxImageBackend

        assert PROVIDER_MINIMAX in get_registered_backends()
        assert isinstance(create_backend(PROVIDER_MINIMAX, api_key="sk"), MiniMaxImageBackend)


class TestTextToImage:
    async def test_t2i_request_build(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_img_response(), download) as route:
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk", model="image-01", base_url="https://api.minimax.io")
            result = await b.generate(ImageGenerationRequest(prompt="a fox", output_path=tmp_path / "o.png"))

        body = request_json(route.calls.last.request)
        assert body["model"] == "image-01"
        assert body["prompt"] == "a fox"
        assert body["response_format"] == "url"
        assert body["n"] == 1
        assert body["prompt_optimizer"] is False
        assert "subject_reference" not in body
        # Default exact 9:16 dimensions cap at 2048 per edge.
        assert (body["width"], body["height"]) == (1152, 2048)
        # Base hosts derive /v1 + /image_generation.
        assert str(route.calls.last.request.url) == "https://api.minimax.io/v1/image_generation"
        assert route.calls.last.request.headers["Authorization"] == "Bearer sk"
        assert result.provider == PROVIDER_MINIMAX
        assert result.model == "image-01"
        assert result.image_uri == "https://x/out.png"
        assert result.image_path == tmp_path / "o.png"
        # Forward response URLs/request output_path unchanged to downloaders.
        download.assert_called_once_with("https://x/out.png", tmp_path / "o.png")

    async def test_default_endpoint_is_domestic(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_img_response(), download) as route:
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

        assert str(route.calls.last.request.url) == "https://api.minimaxi.com/v1/image_generation"

    async def test_seed_passthrough(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_img_response(), download) as route:
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png", seed=42))

        assert request_json(route.calls.last.request)["seed"] == 42

    async def test_no_seed_field_when_unset(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_img_response(), download) as route:
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

        assert "seed" not in request_json(route.calls.last.request)


class TestDimensions:
    async def _dims(self, tmp_path: Path, **req_kwargs) -> tuple[int, int]:
        download = AsyncMock()
        with _generation_route(_img_response(), download) as route:
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png", **req_kwargs))
        body = request_json(route.calls.last.request)
        return body["width"], body["height"]

    async def test_landscape_picks_wide(self, tmp_path: Path):
        assert await self._dims(tmp_path, aspect_ratio="16:9") == (2048, 1152)

    async def test_square(self, tmp_path: Path):
        assert await self._dims(tmp_path, aspect_ratio="1:1") == (1440, 1440)

    async def test_explicit_1k_tier(self, tmp_path: Path):
        assert await self._dims(tmp_path, aspect_ratio="9:16", image_size="1K") == (1008, 1792)

    async def test_custom_pixel_strips_embedded_ratio(self, tmp_path: Path):
        # Custom 1920*1080 contributes min=1080 short edges only; project 9:16 determines ratios.
        w, h = await self._dims(tmp_path, aspect_ratio="9:16", image_size="1920*1080")
        assert w * 16 == h * 9
        assert w < h

    @pytest.mark.parametrize("aspect", ["9:16", "16:9", "1:1", "3:4", "4:3", "2:3", "3:2", "21:9", "5:1"])
    async def test_dims_within_range_and_multiple_of_8(self, tmp_path: Path, aspect: str):
        w, h = await self._dims(tmp_path, aspect_ratio=aspect)
        assert 512 <= w <= 2048
        assert 512 <= h <= 2048
        assert w % 8 == 0
        assert h % 8 == 0

    async def test_extreme_ratio_short_edge_clamped_to_512(self, tmp_path: Path):
        # 5:1 exceeds representable 4:1; computed short edges <512 clamp to 512, still divisible by 8.
        w, h = await self._dims(tmp_path, aspect_ratio="5:1")
        assert h == 512
        assert w == 2040

    async def test_small_custom_size_preserves_ratio(self, tmp_path: Path):
        # Custom short edges <512 clamp to _MIN_EDGE first, preventing aspect_size undersized edges
        # from independent _clamp_edge distortion, such as 16:9 degenerating to 512x512 squares.
        w, h = await self._dims(tmp_path, aspect_ratio="16:9", image_size="320*180")
        assert w >= 512
        assert h >= 512
        assert w > h  # Landscape never degenerates to 1:1.
        assert abs(w / h - 16 / 9) < 0.1


class TestSubjectReference:
    async def test_i2i_single_subject_reference(self, tmp_path: Path):
        download = AsyncMock()
        ref = _make_ref(tmp_path, "face.png")
        with _generation_route(_img_response(), download) as route:
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            await b.generate(
                ImageGenerationRequest(prompt="hero portrait", output_path=tmp_path / "o.png", reference_images=[ref])
            )

        subject = request_json(route.calls.last.request)["subject_reference"]
        assert len(subject) == 1
        assert subject[0]["type"] == "character"
        assert subject[0]["image_file"].startswith("data:image/png;base64,")

    async def test_multiple_refs_rejected_before_submit(self, tmp_path: Path):
        refs = [_make_ref(tmp_path, f"r{i}.png") for i in range(3)]
        with capture_http() as router:
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            backend = MiniMaxImageBackend(api_key="sk")
            from lib.backends.image_capabilities import gate_image_request
            from lib.custom_provider.backends import CustomImageBackend

            backend = CustomImageBackend(
                provider_id="custom-1",
                delegate=backend,
                model=backend.model,
                reference_limit_override=3,
            )
            await gate_image_request(backend, provider_id="custom-1", reference_count=3)
            with pytest.raises(ImageCapabilityError) as exc:
                await backend.generate(
                    ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=refs)
                )

        assert exc.value.code == "image_reference_images_exceeded"
        assert exc.value.params["limit"] == 1
        assert exc.value.params["count"] == 3
        assert not router.calls

    async def test_missing_ref_raises_unreadable(self, tmp_path: Path):
        from lib.backends.image_backends.minimax import MiniMaxImageBackend

        b = MiniMaxImageBackend(api_key="sk")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(
                ImageGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.png",
                    reference_images=[ReferenceImage(path=str(tmp_path / "nope.png"))],
                )
            )
        assert ei.value.code == "image_reference_images_unreadable"
        assert ei.value.params["model"] == "image-01"
        assert ei.value.params["names"] == "nope.png"

    async def test_empty_ref_path_treated_as_missing(self, tmp_path: Path):
        from lib.backends.image_backends.minimax import MiniMaxImageBackend

        b = MiniMaxImageBackend(api_key="sk")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(
                ImageGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.png", reference_images=[ReferenceImage(path="")]
                )
            )
        assert ei.value.code == "image_reference_images_unreadable"
        # Empty paths use locale-neutral #1, never Chinese placeholders.
        assert ei.value.params["names"] == "#1"

    async def test_ref_read_oserror_raises_unreadable(self, tmp_path: Path):
        from lib.backends.image_backends.minimax import MiniMaxImageBackend

        ref = _make_ref(tmp_path, "face.png")
        b = MiniMaxImageBackend(api_key="sk")
        with (
            patch(
                "lib.backends.image_backends.minimax.image_to_base64_data_uri", side_effect=OSError("permission denied")
            ),
            pytest.raises(ImageCapabilityError) as ei,
        ):
            await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=[ref]))
        assert ei.value.code == "image_reference_images_unreadable"
        assert ei.value.params["model"] == "image-01"
        assert ei.value.params["names"] == "face.png"


class TestResponseHandling:
    async def test_base64_response_decoded_and_saved(self, tmp_path: Path):
        raw = b"\x89PNG\r\nhello-bytes"
        b64 = base64.b64encode(raw).decode("ascii")
        download = AsyncMock()
        out = tmp_path / "o.png"
        # Route-backed download makes assert_not_called prove base64 saves independently without downloads.
        with _generation_route(_b64_response(b64), download):
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            result = await b.generate(ImageGenerationRequest(prompt="x", output_path=out))

        assert out.read_bytes() == raw
        # Base64 paths have no remote URLs.
        assert result.image_uri is None
        download.assert_not_called()

    async def test_base64_data_uri_prefix_stripped(self, tmp_path: Path):
        raw = b"PNGDATA"
        b64 = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        out = tmp_path / "o.png"
        with _generation_route(_b64_response(b64)):
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=out))

        assert out.read_bytes() == raw

    async def test_business_error_raises_runtime(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_biz_error_response(1004, "invalid api key"), download) as route:
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            with pytest.raises(RuntimeError) as ei:
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))
        assert "1004" in str(ei.value)
        # Business failures neither retry nor download.
        assert route.call_count == 1
        download.assert_not_called()

    async def test_empty_data_raises_runtime(self, tmp_path: Path):
        resp = httpx.Response(200, json={"data": {}, "base_resp": {"status_code": 0}})
        download = AsyncMock()
        with _generation_route(resp, download):
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            with pytest.raises(RuntimeError, match="缺少 image_urls/image_base64"):
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))
        download.assert_not_called()


class TestHttpErrors:
    async def test_400_surfaces_httpstatuserror_single_call(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_error_response(400), download) as route:
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        assert ei.value.response.status_code == 400
        assert route.call_count == 1
        download.assert_not_called()

    async def test_413_surfaces_httpstatuserror_no_retry(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_error_response(413), download) as route:
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        # Preserve status_code for throat 413 downshifts; fail fast once.
        assert ei.value.response.status_code == 413
        assert route.call_count == 1
        download.assert_not_called()


class TestRetryScope:
    async def test_download_failure_does_not_retrigger_generation(self, tmp_path: Path, poll_clock):
        # Transient download failures retry downloads only, never nonidempotent generation POST, avoiding duplicate images/billing.
        # Stub backoff sleeps so download retries do not wait actual seconds.
        from lib.backends.backend_runtime import VIDEO_POLL_MAX_CONSECUTIVE_FAILURES

        download = AsyncMock(side_effect=httpx.ConnectError("conn reset"))
        with _generation_route(_img_response(), download) as route:
            from lib.backends.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            # Exhausted shared budgets raise RuntimeError retaining last original errors.
            with pytest.raises(RuntimeError, match="conn reset"):
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))
        # One generation POST means one charge; all retries are downloads.
        assert route.call_count == 1
        assert download.call_count == VIDEO_POLL_MAX_CONSECUTIVE_FAILURES


class TestPricing:
    def test_image_01_per_image_flat_cny(self):
        from tests.catalog_support import builtin_price

        amount, currency = builtin_price(PROVIDER_MINIMAX, "image-01", "image", {"output_images": 1})
        assert amount == pytest.approx(0.025)
        assert currency == "CNY"
