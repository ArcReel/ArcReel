"""MiniMaxImageBackend 单元测试（mock httpx，单步同步端点，不打真实 HTTP）。"""

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
from lib.providers import PROVIDER_MINIMAX
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
def _generation_route(
    resp: httpx.Response, download: AsyncMock | None = None
) -> Iterator[respx.Route]:
    """成图端点的出站流：走 respx 在 transport 层拦截。

    端点派生、请求体字段、鉴权头都是「发出去的请求长什么样」的契约，断言落在路由捕获的
    真实请求上；``download`` 给出时同时接管落盘（非 base64 分支的独立下载段）。
    """
    with capture_http() as router:
        route = router.post(url__regex=r"https://[^/]+/v1/image_generation").mock(return_value=resp)
        if download is None:
            yield route
        else:
            with patch("lib.image_backends.minimax.download_image_to_path", download):
                yield route


class TestCapabilities:
    def test_image_01_t2i_and_i2i(self):
        from lib.image_backends.minimax import MiniMaxImageBackend

        b = MiniMaxImageBackend(api_key="sk", model="image-01")
        assert b.name == PROVIDER_MINIMAX
        assert b.model == "image-01"
        assert b.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    def test_default_model_when_unset(self):
        from lib.image_backends.minimax import MiniMaxImageBackend

        assert MiniMaxImageBackend(api_key="sk").model == "image-01"

    def test_registered_in_factory(self):
        from lib.image_backends import create_backend, get_registered_backends
        from lib.image_backends.minimax import MiniMaxImageBackend

        assert PROVIDER_MINIMAX in get_registered_backends()
        assert isinstance(create_backend(PROVIDER_MINIMAX, api_key="sk"), MiniMaxImageBackend)


class TestTextToImage:
    async def test_t2i_request_build(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_img_response(), download) as route:
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk", model="image-01", base_url="https://api.minimax.io")
            result = await b.generate(ImageGenerationRequest(prompt="a fox", output_path=tmp_path / "o.png"))

        body = request_json(route.calls.last.request)
        assert body["model"] == "image-01"
        assert body["prompt"] == "a fox"
        assert body["response_format"] == "url"
        assert body["n"] == 1
        assert body["prompt_optimizer"] is False
        assert "subject_reference" not in body
        # 默认 aspect_ratio=9:16 精确算、受单边 2048 收口
        assert (body["width"], body["height"]) == (1152, 2048)
        # 端点：base host 派生 /v1 + /image_generation
        assert str(route.calls.last.request.url) == "https://api.minimax.io/v1/image_generation"
        assert route.calls.last.request.headers["Authorization"] == "Bearer sk"
        assert result.provider == PROVIDER_MINIMAX
        assert result.model == "image-01"
        assert result.image_uri == "https://x/out.png"
        download.assert_called_once()

    async def test_default_endpoint_is_domestic(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_img_response(), download) as route:
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

        assert str(route.calls.last.request.url) == "https://api.minimaxi.com/v1/image_generation"

    async def test_seed_passthrough(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_img_response(), download) as route:
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png", seed=42))

        assert request_json(route.calls.last.request)["seed"] == 42

    async def test_no_seed_field_when_unset(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_img_response(), download) as route:
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

        assert "seed" not in request_json(route.calls.last.request)


class TestDimensions:
    async def _dims(self, tmp_path: Path, **req_kwargs) -> tuple[int, int]:
        download = AsyncMock()
        with _generation_route(_img_response(), download) as route:
            from lib.image_backends.minimax import MiniMaxImageBackend

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
        # 自定义像素 16:9 的 1920*1080 只贡献 min=1080 当短边，比例仍由项目 aspect_ratio=9:16 决定
        w, h = await self._dims(tmp_path, aspect_ratio="9:16", image_size="1920*1080")
        assert w * 16 == h * 9 and w < h

    @pytest.mark.parametrize("aspect", ["9:16", "16:9", "1:1", "3:4", "4:3", "2:3", "3:2", "21:9", "5:1"])
    async def test_dims_within_range_and_multiple_of_8(self, tmp_path: Path, aspect: str):
        w, h = await self._dims(tmp_path, aspect_ratio=aspect)
        assert 512 <= w <= 2048 and 512 <= h <= 2048
        assert w % 8 == 0 and h % 8 == 0

    async def test_extreme_ratio_short_edge_clamped_to_512(self, tmp_path: Path):
        # 5:1 超出 4:1 可表达上限，短边自然算出 <512 → 夹到 512（仍 8 整除）
        w, h = await self._dims(tmp_path, aspect_ratio="5:1")
        assert h == 512 and w == 2040

    async def test_small_custom_size_preserves_ratio(self, tmp_path: Path):
        # 自定义小尺寸（短边 <512）：短边先夹到 _MIN_EDGE，避免 aspect_size 出 <512 边后
        # 被 _clamp_edge 独立夹取破坏比例（16:9 横屏退化成 512x512 的 1:1）
        w, h = await self._dims(tmp_path, aspect_ratio="16:9", image_size="320*180")
        assert w >= 512 and h >= 512
        assert w > h  # 横屏未退化成 1:1
        assert abs(w / h - 16 / 9) < 0.1


class TestSubjectReference:
    async def test_i2i_single_subject_reference(self, tmp_path: Path):
        download = AsyncMock()
        ref = _make_ref(tmp_path, "face.png")
        with _generation_route(_img_response(), download) as route:
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            await b.generate(
                ImageGenerationRequest(prompt="hero portrait", output_path=tmp_path / "o.png", reference_images=[ref])
            )

        subject = request_json(route.calls.last.request)["subject_reference"]
        assert len(subject) == 1
        assert subject[0]["type"] == "character"
        assert subject[0]["image_file"].startswith("data:image/png;base64,")

    async def test_multiple_refs_truncated_to_first(self, tmp_path: Path):
        download = AsyncMock()
        refs = [_make_ref(tmp_path, f"r{i}.png") for i in range(3)]
        with _generation_route(_img_response(), download) as route:
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=refs))

        # image-01 单脸参考：仅取首张
        subject = request_json(route.calls.last.request)["subject_reference"]
        assert len(subject) == 1

    async def test_missing_ref_raises_unreadable(self, tmp_path: Path):
        from lib.image_backends.minimax import MiniMaxImageBackend

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
        assert ei.value.params["names"] == "nope.png"

    async def test_empty_ref_path_treated_as_missing(self, tmp_path: Path):
        from lib.image_backends.minimax import MiniMaxImageBackend

        b = MiniMaxImageBackend(api_key="sk")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(
                ImageGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.png", reference_images=[ReferenceImage(path="")]
                )
            )
        assert ei.value.code == "image_reference_images_unreadable"
        # 空路径用 locale 中性序号 #1，不漏中文占位
        assert ei.value.params["names"] == "#1"

    async def test_ref_read_oserror_raises_unreadable(self, tmp_path: Path):
        from lib.image_backends.minimax import MiniMaxImageBackend

        ref = _make_ref(tmp_path, "face.png")
        b = MiniMaxImageBackend(api_key="sk")
        with patch("lib.image_backends.minimax.image_to_base64_data_uri", side_effect=OSError("permission denied")):
            with pytest.raises(ImageCapabilityError) as ei:
                await b.generate(
                    ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=[ref])
                )
        assert ei.value.code == "image_reference_images_unreadable"


class TestResponseHandling:
    async def test_base64_response_decoded_and_saved(self, tmp_path: Path):
        raw = b"\x89PNG\r\nhello-bytes"
        b64 = base64.b64encode(raw).decode("ascii")
        download = AsyncMock()
        out = tmp_path / "o.png"
        # 不接管 download：base64 路径独立落盘
        with _generation_route(_b64_response(b64)):
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            result = await b.generate(ImageGenerationRequest(prompt="x", output_path=out))

        assert out.read_bytes() == raw
        # base64 路径无远端 URL
        assert result.image_uri is None
        download.assert_not_called()

    async def test_base64_data_uri_prefix_stripped(self, tmp_path: Path):
        raw = b"PNGDATA"
        b64 = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        out = tmp_path / "o.png"
        with _generation_route(_b64_response(b64)):
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=out))

        assert out.read_bytes() == raw

    async def test_business_error_raises_runtime(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_biz_error_response(1004, "invalid api key"), download) as route:
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            with pytest.raises(RuntimeError) as ei:
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))
        assert "1004" in str(ei.value)
        # 业务错误不重试、不下载
        assert route.call_count == 1
        download.assert_not_called()

    async def test_empty_data_raises_runtime(self, tmp_path: Path):
        resp = httpx.Response(200, json={"data": {}, "base_resp": {"status_code": 0}})
        download = AsyncMock()
        with _generation_route(resp, download) as route:
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            with pytest.raises(RuntimeError):
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))


class TestHttpErrors:
    async def test_400_surfaces_httpstatuserror_single_call(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_error_response(400), download) as route:
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        assert ei.value.response.status_code == 400
        assert route.call_count == 1
        download.assert_not_called()

    async def test_413_surfaces_httpstatuserror_no_retry(self, tmp_path: Path):
        download = AsyncMock()
        with _generation_route(_error_response(413), download) as route:
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        # 保留 status_code 让咽喉层识别 413 走降档；单次 fail-fast
        assert ei.value.response.status_code == 413
        assert route.call_count == 1
        download.assert_not_called()


class TestRetryScope:
    async def test_download_failure_does_not_retrigger_generation(self, tmp_path: Path, poll_clock):
        # 下载阶段瞬态失败只在下载层重试，绝不回退到重跑非幂等的生成 POST（防重复建图 + 重复计费）。
        # 退避 sleep 打桩跳过，避免下载层重试真的等 DOWNLOAD_BACKOFF 秒级时间。
        from lib.retry import DOWNLOAD_MAX_ATTEMPTS

        download = AsyncMock(side_effect=httpx.ConnectError("conn reset"))
        with _generation_route(_img_response(), download) as route:
            from lib.image_backends.minimax import MiniMaxImageBackend

            b = MiniMaxImageBackend(api_key="sk")
            with pytest.raises(httpx.ConnectError):
                await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))
        # 生成 POST 恰好一次（计费一次）；重试全部发生在下载层
        assert route.call_count == 1
        assert download.call_count == DOWNLOAD_MAX_ATTEMPTS


class TestPricing:
    def test_image_01_per_image_flat_cny(self):
        from lib.pricing.lookup import lookup_pricing
        from lib.pricing.strategies import PricingParams, calculate_pricing
        from lib.pricing.types import PerImageFlat

        pricing = lookup_pricing(PROVIDER_MINIMAX, "image-01", "image")
        assert isinstance(pricing, PerImageFlat)
        amount, currency = calculate_pricing(pricing, PricingParams(call_type="image", model="image-01"))
        assert amount == pytest.approx(0.025)
        assert currency == "CNY"
