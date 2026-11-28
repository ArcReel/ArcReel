"""DashScopeViduImageBackend 单元测试（respx 捕获出站请求，异步轮询 + 计价同口径）。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from arcreel_catalog_core import Meter, PriceRow, PriceTable
from lib.backends.endpoint_prefill import infer_builtin_endpoint, infer_endpoint
from lib.backends.http_status_errors import AmbiguousGatewaySubmitError, AmbiguousSubmitError, ProviderRejectedError
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageOutputMissingError,
    ImageRequestFacts,
    ReferenceImage,
)
from lib.backends.image_backends.dashscope import DashScopeViduImageBackend
from lib.backends.providers import PROVIDER_DASHSCOPE
from lib.billing.metering import Priced, Unpriced, UnpricedReason, price_usage
from tests.factories import image_bytes
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, only_request, request_json

_MAAS_HOST = "https://ws-bj.cn-beijing.maas.aliyuncs.com"
_SUBMIT_URL = f"{_MAAS_HOST}/api/v1/services/aigc/image-generation/generation"
_TASK_URL = f"{_MAAS_HOST}/api/v1/tasks/vidu-img-1"
_IMAGE_URL = "https://cdn.example.com/generated-image.png"

_VIDU_IMAGE_PRICE_TABLE = PriceTable(
    currency="CNY",
    meters=(
        Meter(
            meter="output_images",
            dims=("resolution",),
            rows=(
                PriceRow(when={"resolution": "1K"}, price=0.25),
                PriceRow(when={"resolution": "2K"}, price=0.34375),
                PriceRow(when={"resolution": "4K"}, price=0.71875),
            ),
            defaults={"resolution": "1K"},
        ),
    ),
)


def _make_refs(tmp_path: Path, count: int) -> list[ReferenceImage]:
    refs: list[ReferenceImage] = []
    for idx in range(count):
        path = tmp_path / f"ref_{idx}.png"
        path.write_bytes(image_bytes())
        refs.append(ReferenceImage(path=str(path)))
    return refs


def _submit_ok(task_id: str = "vidu-img-1") -> dict:
    return {
        "output": {"task_status": "PENDING", "task_id": task_id},
        "request_id": "req-submit-vidu-1",
    }


def _poll_succeeded(*, sr: str = "1K", size: str = "1376*768", image_count: int = 1) -> dict:
    return {
        "request_id": "req-poll-vidu-1",
        "output": {
            "task_id": "vidu-img-1",
            "task_status": "SUCCEEDED",
            "submit_time": "2026-11-28 20:27:41.291",
            "scheduled_time": "2026-11-28 20:27:41.320",
            "end_time": "2026-11-28 20:28:09.767",
            "finished": True,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": [{"image": _IMAGE_URL, "type": "image"}],
                    },
                }
            ],
        },
        "usage": {"SR": sr, "size": size, "image_count": image_count},
    }


class TestCapabilitiesAndModes:
    async def test_declares_t2i_i2i_and_hard_14_reference_limit(self):
        backend = DashScopeViduImageBackend(api_key="sk-bj", model="vidu/viduq3.1-fast_reference2image")
        assert backend.name == PROVIDER_DASHSCOPE
        assert backend.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
        assert await backend.reference_image_limit() == 14
        assert DashScopeViduImageBackend.CHANNEL_REFERENCE_LIMIT_HARD is True

    @pytest.mark.parametrize("model", ["vidu/viduq3.1-fast_reference2image", "opaque-vidu-alias"])
    async def test_text_to_image_submit_poll_and_download_contract(self, tmp_path: Path, model: str):
        backend = DashScopeViduImageBackend(
            api_key="sk-bj",
            model=model,
            base_url="https://dashscope.aliyuncs.com",
            wan3_base_url=_MAAS_HOST,
        )
        req = ImageGenerationRequest(
            prompt="一间有着精致窗户的花店",
            output_path=tmp_path / "t2i.png",
            aspect_ratio="16:9",
            image_size="1K",
            seed=7,
        )

        with capture_http() as router, bounded_poll_clock():
            submit = router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            poll = router.get(_TASK_URL).mock(
                side_effect=[
                    httpx.Response(200, json={"output": {"task_id": "vidu-img-1", "task_status": "RUNNING"}}),
                    httpx.Response(200, json=_poll_succeeded(sr="1K", size="1376*768")),
                ]
            )
            router.get(_IMAGE_URL).respond(200, content=b"png-bytes")

            result = await backend.generate(req)

        sent = only_request(submit)
        assert sent.headers["Authorization"] == "Bearer sk-bj"
        assert sent.headers["X-DashScope-Async"] == "enable"
        assert request_json(sent) == {
            "model": model,
            "input": {"messages": [{"role": "user", "content": [{"text": "一间有着精致窗户的花店"}]}]},
            "parameters": {
                "resolution": "1K",
                "aspect_ratio": "16:9",
                "n": 1,
                "watermark": False,
                "seed": 7,
            },
        }
        assert poll.call_count == 2
        assert result.image_path.read_bytes() == b"png-bytes"
        assert result.image_uri == _IMAGE_URL
        assert result.usage is not None
        assert result.usage.quantities == {"output_images": 1, "input_images": 0}
        assert result.usage.dimensions == {"resolution": "1K"}

    async def test_reference_to_image_and_edit_up_to_14_references(self, tmp_path: Path):
        refs = _make_refs(tmp_path, 14)
        backend = DashScopeViduImageBackend(
            api_key="sk-bj",
            model="vidu/viduq3.1-fast_reference2image",
            wan3_base_url=f"{_MAAS_HOST}/api/v1/",
        )
        req = ImageGenerationRequest(
            prompt="把背景改成海边日落",
            output_path=tmp_path / "edit.png",
            reference_images=refs,
            aspect_ratio="9:16",
            image_size="4K",
        )

        with capture_http() as router, bounded_poll_clock():
            submit = router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            router.get(_TASK_URL).respond(200, json=_poll_succeeded(sr="4K", size="3072*5504"))
            router.get(_IMAGE_URL).respond(200, content=b"png-4k")

            result = await backend.generate(req)

        body = request_json(only_request(submit))
        content = body["input"]["messages"][0]["content"]
        assert content[0] == {"text": "把背景改成海边日落"}
        assert len(content) == 15
        assert all(part["image"].startswith("data:image/png;base64,") for part in content[1:])
        assert body["parameters"] == {"resolution": "4K", "aspect_ratio": "9:16", "n": 1, "watermark": False}
        assert result.usage is not None
        assert result.usage.quantities == {"output_images": 1, "input_images": 14}
        assert result.usage.dimensions == {"resolution": "4K"}

    def test_falls_back_to_base_url_when_wan3_base_url_unset(self):
        backend = DashScopeViduImageBackend(
            api_key="sk-bj",
            model="vidu/viduq3.1-fast_reference2image",
            base_url=_MAAS_HOST,
        )
        assert backend._request_base_url == f"{_MAAS_HOST}/api/v1"


class TestValidationAndAdmission:
    async def test_rejects_more_than_14_references_before_submit(self, tmp_path: Path):
        refs = _make_refs(tmp_path, 15)
        backend = DashScopeViduImageBackend(api_key="sk-bj", wan3_base_url=_MAAS_HOST)
        req = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=refs)

        with capture_http() as router:
            submit = router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            with pytest.raises(ImageCapabilityError) as exc:
                await backend.generate(req)

        assert exc.value.code == "image_reference_images_exceeded"
        assert exc.value.params["limit"] == 14
        assert exc.value.params["count"] == 15
        assert submit.call_count == 0

    @pytest.mark.parametrize("bad_res", ["512", "auto", "8K", "1024*1024"])
    async def test_rejects_unsupported_resolution_before_submit(self, tmp_path: Path, bad_res: str):
        backend = DashScopeViduImageBackend(api_key="sk-bj", wan3_base_url=_MAAS_HOST)
        req = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", image_size=bad_res)

        with capture_http() as router:
            submit = router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            with pytest.raises(ImageCapabilityError) as exc:
                await backend.generate(req)

        assert exc.value.code == "image_resolution_not_supported"
        assert submit.call_count == 0

    @pytest.mark.parametrize(
        "ratio",
        ["auto", "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "1:4", "4:1", "1:8", "8:1"],
    )
    def test_accepts_all_official_aspect_ratios(self, tmp_path: Path, ratio: str):
        backend = DashScopeViduImageBackend(api_key="sk-bj")
        payload, resolution, ref_count = backend._build_async_payload(
            ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", aspect_ratio=ratio)
        )
        assert payload["parameters"]["aspect_ratio"] == ratio
        assert resolution == "1K"
        assert ref_count == 0

    async def test_rejects_unsupported_aspect_ratio_before_submit(self, tmp_path: Path):
        backend = DashScopeViduImageBackend(api_key="sk-bj", wan3_base_url=_MAAS_HOST)
        req = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", aspect_ratio="5:3")

        with capture_http() as router:
            submit = router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            with pytest.raises(ImageCapabilityError) as exc:
                await backend.generate(req)

        assert exc.value.code == "image_aspect_ratio_not_supported"
        assert submit.call_count == 0


class TestFailuresAndRetryBoundary:
    @pytest.mark.parametrize("status_code", [502, 504])
    async def test_submit_502_and_504_never_retry_adr_0123(self, tmp_path: Path, status_code: int):
        backend = DashScopeViduImageBackend(api_key="sk-bj", wan3_base_url=_MAAS_HOST)
        req = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png")

        with capture_http() as router, bounded_poll_clock():
            submit = router.post(_SUBMIT_URL).respond(status_code, text="gateway timeout")
            with pytest.raises(AmbiguousGatewaySubmitError):
                await backend.generate(req)

        assert submit.call_count == 1

    async def test_submit_read_timeout_is_ambiguous_and_not_retried(self, tmp_path: Path):
        backend = DashScopeViduImageBackend(api_key="sk-bj", wan3_base_url=_MAAS_HOST)
        req = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png")

        with capture_http() as router, bounded_poll_clock():
            submit = router.post(_SUBMIT_URL).mock(side_effect=httpx.ReadTimeout("read timeout"))
            with pytest.raises(AmbiguousSubmitError):
                await backend.generate(req)

        assert submit.call_count == 1

    async def test_submit_connect_error_retries_safely(self, tmp_path: Path):
        backend = DashScopeViduImageBackend(api_key="sk-bj", wan3_base_url=_MAAS_HOST)
        req = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png")

        with capture_http() as router, bounded_poll_clock():
            submit = router.post(_SUBMIT_URL).mock(
                side_effect=[
                    httpx.ConnectError("refused"),
                    httpx.Response(200, json=_submit_ok()),
                ]
            )
            router.get(_TASK_URL).respond(200, json=_poll_succeeded())
            router.get(_IMAGE_URL).respond(200, content=b"png")

            result = await backend.generate(req)

        assert submit.call_count == 2
        assert result.image_path.read_bytes() == b"png"

    async def test_poll_transient_503_recovers_without_resubmitting_post(self, tmp_path: Path):
        backend = DashScopeViduImageBackend(api_key="sk-bj", wan3_base_url=_MAAS_HOST)
        req = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png")

        with capture_http() as router, bounded_poll_clock():
            submit = router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            poll = router.get(_TASK_URL).mock(
                side_effect=[
                    httpx.Response(503, text="busy"),
                    httpx.Response(200, json=_poll_succeeded()),
                ]
            )
            router.get(_IMAGE_URL).respond(200, content=b"png")

            result = await backend.generate(req)

        assert submit.call_count == 1
        assert poll.call_count == 2
        assert result.image_path.read_bytes() == b"png"

    async def test_poll_failed_expired_and_missing_output(self, tmp_path: Path):
        backend = DashScopeViduImageBackend(api_key="sk-bj", wan3_base_url=_MAAS_HOST)
        req = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png")

        with capture_http() as router, bounded_poll_clock():
            router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            router.get(_TASK_URL).respond(
                200,
                json={
                    "request_id": "r-fail",
                    "output": {
                        "task_id": "vidu-img-1",
                        "task_status": "FAILED",
                        "code": "InvalidParameter",
                        "message": "Missing required field 'parameters.n'",
                    },
                },
            )
            with pytest.raises(RuntimeError, match="InvalidParameter"):
                await backend.generate(req)

        with capture_http() as router, bounded_poll_clock():
            router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            router.get(_TASK_URL).respond(
                200,
                json={"request_id": "r-exp", "output": {"task_id": "vidu-img-1", "task_status": "UNKNOWN"}},
            )
            with pytest.raises(RuntimeError, match="过期"):
                await backend.generate(req)

        with capture_http() as router, bounded_poll_clock():
            router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            router.get(_TASK_URL).respond(
                200,
                json={
                    "request_id": "r-empty",
                    "output": {"task_id": "vidu-img-1", "task_status": "SUCCEEDED", "choices": []},
                },
            )
            with pytest.raises(ImageOutputMissingError):
                await backend.generate(req)

    async def test_submit_400_raises_provider_rejected(self, tmp_path: Path):
        backend = DashScopeViduImageBackend(api_key="sk-bj", wan3_base_url=_MAAS_HOST)
        req = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png")

        with capture_http() as router, bounded_poll_clock():
            submit = router.post(_SUBMIT_URL).respond(
                400, json={"code": "InvalidApiKey", "message": "No API-key provided.", "request_id": "r1"}
            )
            with pytest.raises(ProviderRejectedError):
                await backend.generate(req)

        assert submit.call_count == 1


class TestPricingAndPrefillConsistency:
    @pytest.mark.parametrize(
        ("image_size", "expected_tier", "expected_price"),
        [
            (None, "1K", 0.25),
            ("1K", "1K", 0.25),
            ("2k", "2K", 0.34375),
            ("4K", "4K", 0.71875),
        ],
    )
    async def test_estimate_and_settlement_match_official_vidu_prices(
        self, tmp_path: Path, image_size: str | None, expected_tier: str, expected_price: float
    ):
        model = "vidu/viduq3.1-fast_reference2image"
        est_usage = DashScopeViduImageBackend.estimate_usage(
            ImageRequestFacts(model=model, aspect_ratio="16:9", image_size=image_size, reference_count=2)
        )
        est_cost = price_usage(_VIDU_IMAGE_PRICE_TABLE, est_usage, multiplier=1.0, estimate=True)
        assert isinstance(est_cost, Priced)
        assert est_cost.currency == "CNY"
        assert est_cost.amount == pytest.approx(expected_price)

        backend = DashScopeViduImageBackend(api_key="sk-bj", model=model, wan3_base_url=_MAAS_HOST)
        refs = _make_refs(tmp_path, 2)
        req = ImageGenerationRequest(
            prompt="p",
            output_path=tmp_path / "o.png",
            reference_images=refs,
            aspect_ratio="16:9",
            image_size=image_size,
        )
        with capture_http() as router, bounded_poll_clock():
            router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            router.get(_TASK_URL).respond(200, json=_poll_succeeded(sr=expected_tier))
            router.get(_IMAGE_URL).respond(200, content=b"png")
            result = await backend.generate(req)

        assert result.usage is not None
        settled_cost = price_usage(_VIDU_IMAGE_PRICE_TABLE, result.usage, multiplier=1.0)
        assert settled_cost == est_cost

    def test_invalid_spec_in_estimate_usage_stays_unpriced(self):
        model = "vidu/viduq3.1-fast_reference2image"
        bad_res = DashScopeViduImageBackend.estimate_usage(
            ImageRequestFacts(model=model, aspect_ratio="16:9", image_size="8K", reference_count=0)
        )
        assert price_usage(_VIDU_IMAGE_PRICE_TABLE, bad_res, multiplier=1.0, estimate=True) == Unpriced(
            UnpricedReason.NO_USAGE_ESTIMATE
        )
        too_many = DashScopeViduImageBackend.estimate_usage(
            ImageRequestFacts(model=model, aspect_ratio="16:9", image_size="1K", reference_count=15)
        )
        assert price_usage(_VIDU_IMAGE_PRICE_TABLE, too_many, multiplier=1.0, estimate=True) == Unpriced(
            UnpricedReason.NO_USAGE_ESTIMATE
        )

    def test_prefill_maps_dashscope_vidu_image_models_correctly(self):
        model_id = "vidu/viduq3.1-fast_reference2image"
        assert infer_endpoint(model_id, "openai") == "dashscope-vidu-image"
        assert infer_builtin_endpoint("dashscope", "image", model_id) == "dashscope-vidu-image"
        assert infer_endpoint("viduq3-pro", "openai") == "vidu-video-q3-pro"
