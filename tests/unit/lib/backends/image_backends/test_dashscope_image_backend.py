"""DashScopeImageBackend synchronous endpoints, with respx outbound capture."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from lib.backends.http_status_errors import ProviderRejectedError
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.backends.providers import PROVIDER_DASHSCOPE
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, only_request, request_json

_DEFAULT_HOST = "https://dashscope.aliyuncs.com"
_IMAGE_PATH = "/api/v1/services/aigc/multimodal-generation/generation"


def _img_response(url: str = "https://x/out.png") -> httpx.Response:
    return httpx.Response(200, json={"output": {"choices": [{"message": {"content": [{"image": url}]}}]}})


@contextmanager
def _generate_route(
    response: httpx.Response,
    download: AsyncMock,
    *,
    host: str = _DEFAULT_HOST,
) -> Generator[respx.Route]:
    """Intercept generation POST/downloads, yielding routes for real-request assertions."""
    with (
        capture_http() as router,
        patch("lib.backends.image_backends.dashscope.download_image_to_path", download),
    ):
        yield router.post(f"{host}{_IMAGE_PATH}").mock(return_value=response)


def _make_ref(tmp_path: Path, name: str) -> ReferenceImage:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\nfake")
    return ReferenceImage(path=str(p))


def _sent_size(route: respx.Route) -> str:
    return request_json(only_request(route))["parameters"]["size"]


class TestCapabilities:
    def test_qwen_image_20_t2i_and_i2i(self):
        from lib.backends.image_backends.dashscope import DashScopeImageBackend

        b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
        assert b.name == PROVIDER_DASHSCOPE
        assert b.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    def test_wan_image_t2i_and_i2i(self):
        from lib.backends.image_backends.dashscope import DashScopeWanImageBackend

        b = DashScopeWanImageBackend(api_key="sk", model="wan2.7-image-pro")
        assert b.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}


class TestTextToImage:
    async def test_t2i_content_text_only(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0", base_url=_DEFAULT_HOST)
            result = await b.generate(ImageGenerationRequest(prompt="a fox", output_path=tmp_path / "o.png"))

        request = only_request(route)
        body = request_json(request)
        content = body["input"]["messages"][0]["content"]
        assert content == [{"text": "a fox"}]
        # Qwen fusion uses exact default 9:16 within a 2048² budget.
        assert body["parameters"]["size"] == "1440*2560"
        assert body["parameters"]["n"] == 1
        assert body["parameters"]["watermark"] is False
        assert body["parameters"]["prompt_extend"] is False
        # Correct host-derived /api/v1 endpoint path.
        assert request.url.path == _IMAGE_PATH
        assert request.headers["Authorization"] == "Bearer sk"
        assert "X-DashScope-Async" not in request.headers
        assert result.provider == PROVIDER_DASHSCOPE
        assert result.image_uri == "https://x/out.png"
        download.assert_called_once()

    async def test_wan_default_size_follows_aspect(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeWanImageBackend

            b = DashScopeWanImageBackend(api_key="sk", model="wan2.7-image")
            # Wan method-two pixel dimensions preserve default 9:16; default 2K short edge is 1440.
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

        assert _sent_size(route) == "1440*2560"

    async def test_explicit_tier_translated_to_aspect_pixels(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeWanImageBackend

            b = DashScopeWanImageBackend(api_key="sk", model="wan2.7-image")
            # Convert 2K tiers, short edge 1440, into exact-ratio pixels; never pass tier strings that force Wan text-to-image squares.
            await b.generate(
                ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png", aspect_ratio="9:16", image_size="2K")
            )

        assert _sent_size(route) == "1440*2560"

    async def test_custom_pixel_size_strips_embedded_ratio(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeWanImageBackend

            b = DashScopeWanImageBackend(api_key="sk", model="wan2.7-image")
            # Custom 1920*1080 contributes min as short edge; project default 9:16 still determines ratio.
            await b.generate(
                ImageGenerationRequest(
                    prompt="x", output_path=tmp_path / "o.png", aspect_ratio="9:16", image_size="1920*1080"
                )
            )

        # min(1920,1080)=1080 → exact 9:16 with t=8 → 1152*2048, not input 16:9.
        size = _sent_size(route)
        w, h = (int(x) for x in size.split("*"))
        assert w * 16 == h * 9
        assert w < h
        assert size == "1152*2048"

    async def test_low_tier_translated_to_aspect_pixels(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeWanImageBackend

            b = DashScopeWanImageBackend(api_key="sk", model="wan2.7-image")
            # 1K short edge 1024 follows exact ratios.
            await b.generate(
                ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png", aspect_ratio="16:9", image_size="1K")
            )

        assert _sent_size(route) == "1792*1008"

    async def test_landscape_aspect_picks_wide_size(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png", aspect_ratio="16:9"))

        # Exact 16:9 within 2048² budget → 2560*1440.
        assert _sent_size(route) == "2560*1440"

    async def test_qwen_fusion_custom_pixel_strips_embedded_ratio(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
            # Registry Qwen resolutions carry ratios, e.g. 16:9 2688*1536; for project 9:16,
            # use min=1536 short edges and retain 9:16, preventing resolution pixels from overriding project ratios.
            await b.generate(
                ImageGenerationRequest(
                    prompt="x", output_path=tmp_path / "o.png", aspect_ratio="9:16", image_size="2688*1536"
                )
            )

        size = _sent_size(route)
        w, h = (int(x) for x in size.split("*"))
        assert w * 16 == h * 9
        assert w < h
        assert size == "1440*2560"


class TestEditSeriesSize:
    async def test_edit_plus_precise_size_within_per_dim_limit(self, tmp_path: Path):
        download = AsyncMock()
        ref = _make_ref(tmp_path, "ref.png")
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeQwenEditImageBackend

            # Editing edges require [512, 2048]; cap 9:16 at max_long_edge=2048 → exact 1152*2048.
            b = DashScopeQwenEditImageBackend(api_key="sk", model="qwen-image-edit-plus")
            await b.generate(
                ImageGenerationRequest(
                    prompt="edit", output_path=tmp_path / "o.png", aspect_ratio="9:16", reference_images=[ref]
                )
            )

        size = _sent_size(route)
        w, h = (int(x) for x in size.split("*"))
        assert w * 16 == h * 9
        assert max(w, h) <= 2048
        assert size == "1152*2048"


class TestImageToImage:
    async def test_i2i_content_with_images(self, tmp_path: Path):
        download = AsyncMock()
        ref = _make_ref(tmp_path, "ref.png")
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
            await b.generate(
                ImageGenerationRequest(prompt="edit it", output_path=tmp_path / "o.png", reference_images=[ref])
            )

        content = request_json(only_request(route))["input"]["messages"][0]["content"]
        assert content[0]["image"].startswith("data:image/png;base64,")
        assert content[-1] == {"text": "edit it"}

    @pytest.mark.parametrize("model", ["qwen-image-2.0", "wan2.7-image-pro"])
    async def test_channel_sends_every_reference_whatever_the_model_name(self, tmp_path: Path, model: str):
        # Qwen/Wan reference caps live in entries; orchestration crops by effective caps, never channel name-based truncation.
        download = AsyncMock()
        refs = [_make_ref(tmp_path, f"r{i}.png") for i in range(11)]
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model=model)
            await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=refs))

        content = request_json(only_request(route))["input"]["messages"][0]["content"]
        assert len([c for c in content if "image" in c]) == 11
        assert await b.reference_image_limit() is None


class TestCapabilityGating:
    async def test_wan_pro_4k_i2i_raises(self, tmp_path: Path):
        from lib.backends.image_backends.dashscope import DashScopeWanImageBackend

        ref = _make_ref(tmp_path, "ref.png")
        b = DashScopeWanImageBackend(api_key="sk", model="wan2.7-image-pro")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(
                ImageGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.png", image_size="4K", reference_images=[ref]
                )
            )
        assert ei.value.code == "image_dashscope_4k_t2i_only"

    async def test_wan_pro_4k_t2i_allowed(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeWanImageBackend

            b = DashScopeWanImageBackend(api_key="sk", model="wan2.7-image-pro")
            # Pro text-to-image admits 4K, exact-ratio pixels with short edge 2160/budget 4096², never raw 4K tiers.
            await b.generate(
                ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", aspect_ratio="16:9", image_size="4K")
            )
        assert _sent_size(route) == "3840*2160"

    async def test_all_refs_missing_raises(self, tmp_path: Path):
        from lib.backends.image_backends.dashscope import DashScopeImageBackend

        b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(
                ImageGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.png",
                    reference_images=[ReferenceImage(path=str(tmp_path / "nope.png"))],
                )
            )
        # Models support i2i but files are unreadable; use accurate codes, not unsupported-i2i errors.
        assert ei.value.code == "image_reference_images_unreadable"
        assert ei.value.params["model"] == "qwen-image-2.0"

    async def test_empty_ref_path_treated_as_missing(self, tmp_path: Path):
        from lib.backends.image_backends.dashscope import DashScopeImageBackend

        # Empty paths satisfy Path("").exists(); is_file rejects directories, preventing read crashes.
        b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(
                ImageGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.png", reference_images=[ReferenceImage(path="")]
                )
            )
        assert ei.value.code == "image_reference_images_unreadable"
        # Empty paths lack names; locale-neutral #N must replace Chinese placeholders in en/vi errors.
        assert ei.value.params["names"] == "#1"
        assert "空路径" not in ei.value.params["names"]

    async def test_narrow_size_within_budget_not_gated(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeWanImageBackend

            # Narrow 4096*512=2.1M is within 2048×2048=4.19M; gates must not reject
            # by individual dimensions rather than total pixels. min(4096,512)=512 provides short edges,
            # with default project 9:16 replacing embedded 8:1.
            b = DashScopeWanImageBackend(api_key="sk", model="wan2.7-image")
            await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", image_size="4096*512"))
        assert _sent_size(route) == "576*1024"

    async def test_all_refs_unreadable_oserror_raises(self, tmp_path: Path):
        from lib.backends.image_backends.dashscope import DashScopeImageBackend

        # Existing files raising read OSError for permissions/I/O all skip then report precise codes, never 500.
        ref = _make_ref(tmp_path, "ref.png")
        b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
        with (
            patch("lib.backends.image_backends.dashscope.image_to_data_uri", side_effect=OSError("permission denied")),
            pytest.raises(ImageCapabilityError) as ei,
        ):
            await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=[ref]))
        assert ei.value.code == "image_reference_images_unreadable"

    async def test_partial_unreadable_refs_fail_loud(self, tmp_path: Path):
        download = AsyncMock()
        r1, r2 = _make_ref(tmp_path, "a.png"), _make_ref(tmp_path, "b.png")

        def fake_uri(p: Path) -> str:
            if p.name == "a.png":
                raise OSError("io error")
            return "data:image/png;base64,OK"

        with (
            _generate_route(_img_response(), download) as route,
            patch("lib.backends.image_backends.dashscope.image_to_data_uri", side_effect=fake_uri),
        ):
            from lib.backends.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
            # Fail loudly on unreadable a.png, never silently generate b.png subsets; name unreadable files.
            with pytest.raises(ImageCapabilityError) as ei:
                await b.generate(
                    ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=[r1, r2])
                )
        assert ei.value.code == "image_reference_images_unreadable"
        assert "a.png" in ei.value.params["names"]
        assert route.call_count == 0

    async def test_unreadable_names_locale_neutral_separator(self, tmp_path: Path):
        from lib.backends.image_backends.dashscope import DashScopeImageBackend

        # en/vi names use locale-neutral ", " separators, never Chinese 、.
        b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
        missing = [ReferenceImage(path=str(tmp_path / "a.png")), ReferenceImage(path=str(tmp_path / "b.png"))]
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(
                ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=missing)
            )
        names = ei.value.params["names"]
        assert names == "a.png, b.png"
        assert "、" not in names


class TestErrorResponse:
    async def test_http_error_surfaces_httpstatuserror(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(httpx.Response(400, text="bad request"), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        # raise_for_status preserves statuses; 4xx fails fast.
        assert ei.value.response.status_code == 400
        assert route.call_count == 1
        download.assert_not_called()

    async def test_submit_4xx_with_transient_substring_no_retry(self, tmp_path: Path):
        # 4xx messages may contain URL 503 substrings; transient detection reads only
        # response.status_code, not error strings, failing fast at 400 with one billed POST
        # and no download.
        download = AsyncMock()
        host = "https://dashscope-503.example.com"
        with (
            bounded_poll_clock(),
            _generate_route(httpx.Response(400, text="bad request"), download, host=host) as route,
        ):
            from lib.backends.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0", base_url=host)
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        # Real transient-looking error substrings still fail fast once by status.
        assert "503" in str(ei.value)
        assert ei.value.response.status_code == 400
        assert route.call_count == 1
        download.assert_not_called()

    async def test_413_surfaces_httpstatuserror_no_retry(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(httpx.Response(413, text="Request Entity Too Large"), download) as route:
            from lib.backends.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        # Preserve status_code so throat layers recognize 413/downshift; 413 is nonretryable and fails once.
        assert ei.value.response.status_code == 413
        assert route.call_count == 1
        download.assert_not_called()

    async def test_rejection_body_becomes_a_redacted_provider_reason(self, tmp_path: Path):
        """Image 4xx matches video handling: attach sanitized/truncated rejection summaries to exceptions."""
        download = AsyncMock()
        rejection = httpx.Response(
            400, json={"code": "DataInspectionFailed", "message": "input data may contain inappropriate content"}
        )
        with _generate_route(rejection, download):
            from lib.backends.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
            with pytest.raises(ProviderRejectedError) as ei:
                await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))

        assert ei.value.provider_reason == "DataInspectionFailed: input data may contain inappropriate content"
        download.assert_not_called()
