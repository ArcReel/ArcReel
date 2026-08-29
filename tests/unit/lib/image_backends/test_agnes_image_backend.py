"""AgnesImageBackend 单元测试（respx 捕获出站请求，单步同步 OpenAI 兼容端点）。"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from lib.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.providers import PROVIDER_AGNES
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
def _generate_route(response: httpx.Response, download: AsyncMock | None = None) -> Iterator[respx.Route]:
    """拦截建图 POST 并（可选）挡住产物下载，产出该路由供断言真实请求。"""
    with capture_http() as router:
        route = router.post(_ENDPOINT).mock(return_value=response)
        if download is None:
            yield route
        else:
            with patch("lib.image_backends.agnes.download_image_to_path", download):
                yield route


class TestCapabilities:
    def test_t2i_and_i2i(self):
        from lib.image_backends.agnes import AgnesImageBackend

        b = AgnesImageBackend(api_key="sk", model="agnes-image-2.1-flash")
        assert b.name == PROVIDER_AGNES
        assert b.model == "agnes-image-2.1-flash"
        assert b.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    def test_default_model_when_unset(self):
        from lib.image_backends.agnes import AgnesImageBackend

        assert AgnesImageBackend(api_key="sk").model == "agnes-image-2.1-flash"

    def test_registered_in_factory(self):
        from lib.image_backends import create_backend, get_registered_backends
        from lib.image_backends.agnes import AgnesImageBackend

        assert PROVIDER_AGNES in get_registered_backends()
        assert isinstance(create_backend(PROVIDER_AGNES, api_key="sk"), AgnesImageBackend)


class TestTextToImage:
    async def test_t2i_request_build(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk", model="agnes-image-2.1-flash", base_url="https://apihub.agnes-ai.com")
            result = await b.generate(ImageGenerationRequest(prompt="a fox", output_path=tmp_path / "o.png"))

        request = only_request(route)
        body = request_json(request)
        assert body["model"] == "agnes-image-2.1-flash"
        assert body["prompt"] == "a fox"
        assert body["n"] == 1
        # 上游 litellm 网关拒绝 response_format（UnsupportedParamsError）——不得下发
        assert "response_format" not in body
        assert "image" not in body
        # 默认 aspect_ratio=9:16 精确算、受单边 2048 收口
        assert body["size"] == "1152x2048"
        # 端点：base host 派生 /v1 + /images/generations
        assert str(request.url) == _ENDPOINT
        assert request.headers["Authorization"] == "Bearer sk"
        assert result.provider == PROVIDER_AGNES
        assert result.model == "agnes-image-2.1-flash"
        assert result.image_uri == "https://x/out.png"
        download.assert_called_once()

    async def test_default_endpoint(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

        assert str(only_request(route).url) == _ENDPOINT


class TestDimensions:
    async def _size(self, tmp_path: Path, **req_kwargs) -> str:
        download = AsyncMock()
        with _generate_route(_img_response(), download) as route:
            from lib.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png", **req_kwargs))
        return request_json(only_request(route))["size"]

    async def test_landscape_picks_wide(self, tmp_path: Path):
        assert await self._size(tmp_path, aspect_ratio="16:9") == "2048x1152"

    async def test_square(self, tmp_path: Path):
        assert await self._size(tmp_path, aspect_ratio="1:1") == "1440x1440"

    async def test_explicit_1k_tier(self, tmp_path: Path):
        assert await self._size(tmp_path, aspect_ratio="9:16", image_size="1K") == "1008x1792"

    async def test_custom_pixel_strips_embedded_ratio(self, tmp_path: Path):
        # 自定义像素 16:9 的 1920*1080 只贡献 min=1080 当短边，比例仍由项目 aspect_ratio=9:16 决定
        size = await self._size(tmp_path, aspect_ratio="9:16", image_size="1920*1080")
        w, h = (int(v) for v in size.split("x"))
        assert w * 16 == h * 9 and w < h

    @pytest.mark.parametrize("aspect", ["9:16", "16:9", "1:1", "3:4", "4:3", "2:3", "3:2"])
    async def test_dims_multiple_of_8(self, tmp_path: Path, aspect: str):
        size = await self._size(tmp_path, aspect_ratio=aspect)
        w, h = (int(v) for v in size.split("x"))
        assert w % 8 == 0 and h % 8 == 0
        assert max(w, h) <= 2048


class TestImageToImage:
    async def test_i2i_reference_images_as_data_uri_list(self, tmp_path: Path):
        download = AsyncMock()
        refs = [_make_ref(tmp_path, f"r{i}.png") for i in range(2)]
        with _generate_route(_img_response(), download) as route:
            from lib.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            await b.generate(
                ImageGenerationRequest(prompt="hero", output_path=tmp_path / "o.png", reference_images=refs)
            )

        body = request_json(only_request(route))
        images = body["image"]
        assert isinstance(images, list)
        assert len(images) == 2
        assert all(item.startswith("data:image/png;base64,") for item in images)
        # I2I 仍显式下发 size
        assert "size" in body

    async def test_missing_ref_raises_unreadable(self, tmp_path: Path):
        from lib.image_backends.agnes import AgnesImageBackend

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
        from lib.image_backends.agnes import AgnesImageBackend

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
            from lib.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            result = await b.generate(ImageGenerationRequest(prompt="x", output_path=out))

        assert out.read_bytes() == raw
        # base64 路径无远端 URL
        assert result.image_uri is None

    async def test_base64_data_uri_prefix_stripped(self, tmp_path: Path):
        raw = b"PNGDATA"
        b64 = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        out = tmp_path / "o.png"
        with _generate_route(_b64_response(b64)):
            from lib.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=out))

        assert out.read_bytes() == raw

    async def test_empty_data_raises_runtime(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(httpx.Response(200, json={"created": 1, "data": []}), download):
            from lib.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            with pytest.raises(RuntimeError):
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

    async def test_url_download_failure_falls_back_to_b64(self, tmp_path: Path):
        # URL 下载失败但同响应带 b64_json：回退落盘，不丢弃已计费的成功生成
        raw = b"\x89PNG\r\nfallback"
        b64 = base64.b64encode(raw).decode("ascii")
        response = httpx.Response(200, json={"created": 1, "data": [{"url": "https://x/out.png", "b64_json": b64}]})
        download = AsyncMock(side_effect=RuntimeError("download boom"))
        out = tmp_path / "o.png"
        with _generate_route(response, download):
            from lib.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            result = await b.generate(ImageGenerationRequest(prompt="x", output_path=out))

        assert out.read_bytes() == raw
        # 走 b64 回退路径，无远端 URL
        assert result.image_uri is None
        download.assert_called()

    async def test_url_download_failure_without_b64_still_raises(self, tmp_path: Path):
        # 仅有 url 且下载失败、无 b64 兜底：照常上抛，不静默吞错
        download = AsyncMock(side_effect=RuntimeError("download boom"))
        with _generate_route(_img_response(), download):
            from lib.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            with pytest.raises(RuntimeError):
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

    async def test_missing_url_b64_log_redacts_body(self, tmp_path: Path, caplog):
        # 响应缺 url/b64 时只记键名与 data 条数，敏感字段值不落日志
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
            from lib.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            with pytest.raises(RuntimeError):
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

        log_text = caplog.text
        assert "secret-prompt" not in log_text
        assert "sig=abc" not in log_text
        # 键名进日志便于诊断
        assert "signed_url" in log_text


class TestHttpErrors:
    async def test_400_surfaces_httpstatuserror_single_call(self, tmp_path: Path):
        download = AsyncMock()
        with _generate_route(httpx.Response(400, text="boom"), download) as route:
            from lib.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        assert ei.value.response.status_code == 400
        assert route.call_count == 1
        download.assert_not_called()


class TestRetryScope:
    async def test_download_failure_does_not_retrigger_generation(self, tmp_path: Path):
        # 下载阶段瞬态失败只在下载层重试，绝不回退到重跑非幂等的生成 POST（防重复建图 + 重复计费）。
        from lib.video_backends.base import VIDEO_POLL_MAX_CONSECUTIVE_FAILURES

        download = AsyncMock(side_effect=httpx.ConnectError("conn reset"))
        with bounded_poll_clock(), _generate_route(_img_response(), download) as route:
            from lib.image_backends.agnes import AgnesImageBackend

            b = AgnesImageBackend(api_key="sk")
            # 共用预算耗尽后抛的是带最后一次原错误的 RuntimeError。
            with pytest.raises(RuntimeError, match="conn reset"):
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))
        # 生成 POST 恰好一次（计费一次）；重试全部发生在下载层
        assert route.call_count == 1
        assert download.call_count == VIDEO_POLL_MAX_CONSECUTIVE_FAILURES


class TestPricing:
    def test_per_image_flat_usd(self):
        from lib.pricing.lookup import lookup_pricing
        from lib.pricing.strategies import PricingParams, calculate_pricing
        from lib.pricing.types import PerImageFlat

        pricing = lookup_pricing(PROVIDER_AGNES, "agnes-image-2.1-flash", "image")
        assert isinstance(pricing, PerImageFlat)
        amount, currency = calculate_pricing(pricing, PricingParams(call_type="image", model="agnes-image-2.1-flash"))
        assert amount == pytest.approx(0.003)
        assert currency == "USD"
