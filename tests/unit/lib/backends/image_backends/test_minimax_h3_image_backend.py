"""MiniMaxH3ImageBackend 单元测试（respx 拦截全部出站 HTTP，不调用真实供应商）。

请求与响应形状取自官方文档：
https://platform.minimax.cn/docs/api-reference/image-generation-h3-generations
https://platform.minimax.cn/docs/api-reference/image-generation-h3-edits
"""

from __future__ import annotations

import base64
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import respx

from arcreel_catalog_core import (
    Meter,
    PriceRow,
    PriceTable,
    check_catalog,
    dump_entry,
    endpoint_version_floor,
    parse_catalog,
)
from lib.backends.http_status_errors import AmbiguousSubmitError
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageOutputMissingError,
    ImageRequestFacts,
    ReferenceImage,
)
from lib.backends.image_backends.minimax import MiniMaxH3ImageBackend
from lib.backends.providers import PROVIDER_MINIMAX
from lib.billing.metering import Priced, Unpriced, UnpricedReason, price_usage
from lib.catalog import query as catalog
from tests.http_capture import (
    capture_http,
    only_request,
    request_form_fields,
    request_form_files,
    request_json,
)

# RFC 5737 TEST-NET-3 公网文档地址：既经过 check_destination 的真实地址校验，又不发起 DNS 查询。
_BASE_URL = "https://203.0.113.10"
_GENERATIONS = r"https://[^/]+/v1/images/generations"
_EDITS = r"https://[^/]+/v1/images/edits"
_MODELS = ("MiniMax-H3-Image", "MiniMax-H3-Image-Highspeed")
_RESULT_URL = "https://203.0.113.7/out.png"
_DOWNLOADED_BYTES = b"\x89PNG\r\n\x1a\nh3-image"

_USAGE = {
    "generated_images": 1,
    "total_tokens": 4300,
    "input_tokens": 300,
    "output_tokens": 4000,
    "input_tokens_details": {"text_tokens": 120, "image_tokens": 180},
    "output_tokens_details": {"image_tokens": 4000},
}


def _image_response(*, item: dict | None = None, usage: dict | None = None) -> httpx.Response:
    body: dict = {
        "created": 1763000000,
        "data": [item if item is not None else {"url": _RESULT_URL, "size": "1536x2720", "revised_prompt": "p"}],
        "output_format": "png",
        "request_id": "req-1",
    }
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def _refs(tmp_path: Path, count: int, suffix: str = ".png") -> list[ReferenceImage]:
    refs = []
    for index in range(count):
        path = tmp_path / f"ref-{index}{suffix}"
        path.write_bytes(f"img-{index}".encode())
        refs.append(ReferenceImage(path=str(path)))
    return refs


@contextmanager
def _routes(response: httpx.Response | list[httpx.Response]) -> Generator[tuple[respx.Route, respx.Route, respx.Route]]:
    """文生图、参考图编辑与结果 URL 下载三条真实 httpx 路由。"""
    with capture_http() as router:
        mock = {"side_effect": response} if isinstance(response, list) else {"return_value": response}
        generations = router.post(url__regex=_GENERATIONS).mock(**mock)
        edits = router.post(url__regex=_EDITS).mock(**mock)
        download = router.get(_RESULT_URL).mock(return_value=httpx.Response(200, content=_DOWNLOADED_BYTES))
        yield generations, edits, download


def _backend(model: str = "MiniMax-H3-Image", **kwargs) -> MiniMaxH3ImageBackend:
    return MiniMaxH3ImageBackend(api_key="sk", model=model, base_url=_BASE_URL, **kwargs)


class TestChannelFacts:
    def test_both_capabilities_and_sixteen_references(self):
        backend = _backend()
        assert backend.name == PROVIDER_MINIMAX
        assert backend.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
        assert MiniMaxH3ImageBackend.CHANNEL_REFERENCE_LIMIT == 16

    def test_registered_under_its_endpoint(self):
        from lib.backends.image_backends import create_backend

        backend = create_backend("minimax-h3-image", api_key="sk", model="MiniMax-H3-Image-Highspeed")
        assert isinstance(backend, MiniMaxH3ImageBackend)
        assert backend.model == "MiniMax-H3-Image-Highspeed"

    def test_builtin_minimax_provider_routes_the_endpoint_to_this_channel(self):
        from lib.backends.backend_assembly.specs import PROVIDER_ENDPOINT_SPECS, PROVIDER_ENDPOINTS

        declared = PROVIDER_ENDPOINTS[("minimax", "image")]
        assert declared.endpoints == ("minimax-image", "minimax-h3-image")
        # image-01 仍是默认端点
        assert declared.default == "minimax-image"
        assert PROVIDER_ENDPOINT_SPECS[("minimax", "minimax-h3-image")].registry_backend == "minimax-h3-image"


class TestTextToImage:
    @pytest.mark.parametrize("model", _MODELS)
    async def test_posts_json_to_generations_and_saves_the_result(self, tmp_path: Path, model: str):
        output = tmp_path / "o.png"
        with _routes(_image_response(usage=_USAGE)) as (generations, edits, download):
            result = await _backend(model).generate(ImageGenerationRequest(prompt="一只狐狸", output_path=output))

        request = only_request(generations)
        assert not edits.called
        assert str(request.url) == f"{_BASE_URL}/v1/images/generations"
        assert request.headers["Authorization"] == "Bearer sk"
        assert request_json(request) == {
            "model": model,
            "prompt": "一只狐狸",
            "n": 1,
            "response_format": "url",
            "output_format": "png",
            "aspect_ratio": "9:16",
            "area": "2K",
            "disable_prompt_enhanced": True,
        }
        assert download.call_count == 1
        assert output.read_bytes() == _DOWNLOADED_BYTES
        assert result.model == model
        assert result.image_uri == _RESULT_URL

    async def test_submission_waits_at_least_the_documented_600_seconds(self, tmp_path: Path):
        with _routes(_image_response()) as (generations, _, _download):
            await _backend().generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))

        assert only_request(generations).extensions["timeout"]["read"] >= 600

    async def test_b64_json_result_is_written_to_the_output_path(self, tmp_path: Path):
        output = tmp_path / "o.png"
        payload = base64.b64encode(b"\x89PNG-bytes").decode()
        with _routes(_image_response(item={"b64_json": payload, "size": "1024x1024"})) as (_, _, download):
            result = await _backend().generate(ImageGenerationRequest(prompt="p", output_path=output))

        assert output.read_bytes() == b"\x89PNG-bytes"
        assert result.image_uri is None
        assert not download.called

    @pytest.mark.parametrize(("suffix", "output_format"), [(".jpg", "jpeg"), (".webp", "webp"), (".png", "png")])
    async def test_output_format_follows_the_output_path(self, tmp_path: Path, suffix: str, output_format: str):
        with _routes(_image_response()) as (generations, _, _download):
            await _backend().generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / f"o{suffix}"))

        assert request_json(only_request(generations))["output_format"] == output_format

    async def test_failed_image_item_reports_missing_output(self, tmp_path: Path):
        item = {"error": {"code": 1027, "message": "content safety"}}
        with _routes(_image_response(item=item)) as (_, _, download), pytest.raises(ImageOutputMissingError):
            await _backend().generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        assert not download.called


class TestReferenceEdit:
    @pytest.mark.parametrize("model", _MODELS)
    async def test_posts_multipart_to_edits_with_references_in_order(self, tmp_path: Path, model: str):
        refs = _refs(tmp_path, 3)
        jpeg = tmp_path / "late.jpg"
        jpeg.write_bytes(b"jpeg-bytes")
        refs.append(ReferenceImage(path=str(jpeg)))
        output = tmp_path / "o.png"
        with _routes(_image_response(usage=_USAGE)) as (generations, edits, download):
            await _backend(model).generate(
                ImageGenerationRequest(prompt="图1 的人站在图4 的街口", output_path=output, reference_images=refs)
            )

        request = only_request(edits)
        assert not generations.called
        assert str(request.url) == f"{_BASE_URL}/v1/images/edits"
        assert request.headers["Authorization"] == "Bearer sk"
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert request_form_fields(request) == {
            "model": model,
            "prompt": "图1 的人站在图4 的街口",
            "n": "1",
            "response_format": "url",
            "output_format": "png",
            "aspect_ratio": "9:16",
            "area": "2K",
        }
        assert request_form_files(request) == [
            ("image[]", "ref-0.png", "image/png", b"img-0"),
            ("image[]", "ref-1.png", "image/png", b"img-1"),
            ("image[]", "ref-2.png", "image/png", b"img-2"),
            ("image[]", "late.jpg", "image/jpeg", b"jpeg-bytes"),
        ]
        assert download.call_count == 1
        assert output.read_bytes() == _DOWNLOADED_BYTES

    async def test_sixteen_references_are_all_sent(self, tmp_path: Path):
        with _routes(_image_response()) as (_, edits, _download):
            await _backend().generate(
                ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=_refs(tmp_path, 16))
            )

        assert [name for _, name, _, _ in request_form_files(only_request(edits))] == [
            f"ref-{i}.png" for i in range(16)
        ]

    async def test_seventeen_references_are_rejected_before_submission(self, tmp_path: Path):
        with _routes(_image_response()) as (generations, edits, _download), pytest.raises(ImageCapabilityError) as exc:
            await _backend().generate(
                ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=_refs(tmp_path, 17))
            )

        assert exc.value.code == "image_reference_images_exceeded"
        assert exc.value.params == {"model": "MiniMax-H3-Image", "limit": 16, "count": 17}
        assert not generations.called
        assert not edits.called

    async def test_missing_reference_is_rejected_instead_of_skipped(self, tmp_path: Path):
        refs = [*_refs(tmp_path, 1), ReferenceImage(path=str(tmp_path / "gone.png"))]
        with _routes(_image_response()) as (generations, edits, _download), pytest.raises(ImageCapabilityError) as exc:
            await _backend().generate(
                ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=refs)
            )

        assert exc.value.code == "image_reference_images_unreadable"
        assert exc.value.params["names"] == "gone.png"
        assert not generations.called
        assert not edits.called


class TestSizeMapping:
    @pytest.mark.parametrize(
        ("image_size", "aspect_ratio", "expected"),
        [
            (None, "9:16", {"aspect_ratio": "9:16", "area": "2K"}),
            ("1K", "1:1", {"aspect_ratio": "1:1", "area": "1K"}),
            ("4k", "16:9", {"aspect_ratio": "16:9", "area": "4K"}),
            ("2K", "1080:1920", {"aspect_ratio": "9:16", "area": "2K"}),
            ("2K", "3:1", {"aspect_ratio": "3:1", "area": "2K"}),
            # 自定义宽x高只取短边，比例来自项目；显式 size 不与 aspect_ratio、area 同发
            ("1920x1080", "16:9", {"size": "1920x1080"}),
            ("2560*1440", "9:16", {"size": "1440x2560"}),
        ],
    )
    async def test_size_fields(self, tmp_path: Path, image_size, aspect_ratio, expected):
        with _routes(_image_response()) as (generations, _, _download):
            await _backend().generate(
                ImageGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.png", image_size=image_size, aspect_ratio=aspect_ratio
                )
            )

        body = request_json(only_request(generations))
        assert {key: body[key] for key in ("size", "aspect_ratio", "area") if key in body} == expected

    @pytest.mark.parametrize(
        ("image_size", "aspect_ratio", "code"),
        [
            ("2K", "4:1", "image_aspect_ratio_not_supported"),
            ("2K", "1:5", "image_aspect_ratio_not_supported"),
            ("512px", "1:1", "image_resolution_not_supported"),
            # 面积下限 655,360、上限 20,000,000
            ("600x600", "1:1", "image_resolution_not_supported"),
            ("5000x5000", "1:1", "image_resolution_not_supported"),
            ("huge", "1:1", "image_resolution_not_supported"),
        ],
    )
    async def test_unsupported_input_fails_before_submission(self, tmp_path: Path, image_size, aspect_ratio, code):
        with _routes(_image_response()) as (generations, edits, _download), pytest.raises(ImageCapabilityError) as exc:
            await _backend().generate(
                ImageGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.png",
                    image_size=image_size,
                    aspect_ratio=aspect_ratio,
                    reference_images=_refs(tmp_path, 1),
                )
            )

        assert exc.value.code == code
        assert not generations.called
        assert not edits.called


class TestUsage:
    async def test_token_usage_is_settled_from_the_response(self, tmp_path: Path):
        with _routes(_image_response(usage=_USAGE)) as _:
            result = await _backend().generate(
                ImageGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.png", reference_images=_refs(tmp_path, 1), image_size="4K"
                )
            )

        assert result.usage is not None
        assert dict(result.usage.quantities) == {
            "uncached_input_tokens": 120,
            "uncached_image_input_tokens": 180,
            "image_output_tokens": 4000,
        }
        assert dict(result.usage.dimensions) == {"resolution": "4K"}
        assert (result.text_input_tokens, result.image_input_tokens, result.image_output_tokens) == (120, 180, 4000)

    async def test_missing_usage_reports_only_the_image_count(self, tmp_path: Path):
        with _routes(_image_response()) as _:
            result = await _backend().generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))

        assert result.usage is not None
        assert dict(result.usage.quantities) == {"output_images": 1}
        assert result.image_output_tokens is None

    def test_estimate_stays_unknown_under_token_price_table_and_preserves_resolution_dimension(self):
        from lib.custom_provider.metering import with_image_counts

        usage = MiniMaxH3ImageBackend.estimate_usage(
            ImageRequestFacts(model="MiniMax-H3-Image", aspect_ratio="9:16", image_size="4k", reference_count=2)
        )
        assert dict(usage.dimensions) == {"resolution": "4K"}
        token_table = PriceTable(
            currency="CNY",
            meters=tuple(
                Meter(meter=meter, dims=(), rows=(PriceRow(when={}, price=price),))
                for meter, price in (
                    ("uncached_input_tokens", 15.0),
                    ("uncached_image_input_tokens", 10.0),
                    ("image_output_tokens", 25.0),
                )
            ),
        )
        assert price_usage(token_table, usage, multiplier=1.0, estimate=True) == Unpriced(
            UnpricedReason.NO_USAGE_ESTIMATE
        )
        # 自定义供应商下按张分分辨率档计价时，预估与结算同用 resolution 维度
        per_image_table = PriceTable(
            currency="CNY",
            meters=(
                Meter(
                    meter="output_images", dims=("resolution",), rows=(PriceRow(when={"resolution": "4K"}, price=0.5),)
                ),
            ),
        )
        assert price_usage(per_image_table, with_image_counts(usage, 2), multiplier=1.0, estimate=True) == Priced(
            0.5, "CNY"
        )


class TestSubmissionIsNotRepeated:
    async def test_read_timeout_fails_without_resubmitting(self, tmp_path: Path, poll_clock):
        with _routes(_image_response()) as (generations, _, download):
            generations.side_effect = httpx.ReadTimeout("read timed out")
            with pytest.raises(AmbiguousSubmitError):
                await _backend().generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))

        assert generations.call_count == 1
        assert not download.called

    @pytest.mark.parametrize("status", [500, 502, 504, 408])
    async def test_server_or_gateway_failure_is_not_resubmitted(self, tmp_path: Path, poll_clock, status: int):
        error = httpx.Response(status, json={"type": "error", "error": {"type": "server_error", "message": "x"}})
        with _routes(error) as (_, edits, _download), pytest.raises(httpx.HTTPStatusError):
            await _backend().generate(
                ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=_refs(tmp_path, 1))
            )

        assert edits.call_count == 1

    async def test_rate_limited_submission_is_retried(self, tmp_path: Path, poll_clock):
        limited = httpx.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "x"}})
        with _routes([limited, _image_response()]) as (generations, _, download):
            await _backend().generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))

        assert generations.call_count == 2
        assert download.call_count == 1

    async def test_download_failure_is_retried_without_resubmitting(self, tmp_path: Path, poll_clock):
        from lib.backends.backend_runtime import VIDEO_POLL_MAX_CONSECUTIVE_FAILURES

        with _routes(_image_response()) as (generations, _, download):
            download.mock(side_effect=httpx.ConnectError("conn reset"))
            with pytest.raises(RuntimeError, match="conn reset"):
                await _backend().generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))

        assert generations.call_count == 1
        assert download.call_count == VIDEO_POLL_MAX_CONSECUTIVE_FAILURES


class TestCustomProviderEndpoint:
    async def test_generate_and_edit_paths_can_be_overridden_separately(self, tmp_path: Path):
        from types import SimpleNamespace

        from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

        provider = SimpleNamespace(
            id=7, provider_id="custom-7", api_key="sk", base_url="https://203.0.113.11/v1", discovery_format="openai"
        )
        backend = ENDPOINT_REGISTRY["minimax-h3-image"].build_backend(
            provider, "MiniMax-H3-Image", {"edit": "/h3/edits"}
        )
        with capture_http() as router:
            edits = router.post("https://203.0.113.11/v1/h3/edits").mock(return_value=_image_response())
            generations = router.post("https://203.0.113.11/v1/images/generations").mock(return_value=_image_response())
            download = router.get(_RESULT_URL).mock(return_value=httpx.Response(200, content=_DOWNLOADED_BYTES))
            await backend.generate(
                ImageGenerationRequest(prompt="p", output_path=tmp_path / "a.png", reference_images=_refs(tmp_path, 1))
            )
            await backend.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "b.png"))

        assert edits.call_count == 1
        assert generations.call_count == 1
        assert download.call_count == 2


class TestCatalogAndVersionFloor:
    @staticmethod
    def _raw_catalog() -> dict:
        floor = endpoint_version_floor("minimax", "image", "minimax-h3-image")
        assert floor == "0.37.1"
        base = catalog.factory_snapshot()
        image_01 = next(e for e in base.entries if e.maker == "minimax" and e.name == "image-01")
        h3_entries = [
            {
                "maker": "minimax",
                "name": name,
                "media_type": "image",
                "display_name": name.replace("-", " "),
                "facts": {
                    "text_to_image": True,
                    "image_to_image": True,
                    "resolutions": ["1K", "2K", "4K"],
                    "max_reference_images": 16,
                },
                "price": {
                    "currency": "CNY",
                    "meters": [
                        {"meter": "uncached_input_tokens", "dims": [], "rows": [{"when": {}, "price": 15.0}]},
                        {"meter": "uncached_image_input_tokens", "dims": [], "rows": [{"when": {}, "price": img_in}]},
                        {"meter": "image_output_tokens", "dims": [], "rows": [{"when": {}, "price": img_out}]},
                    ],
                },
            }
            for name, img_in, img_out in (
                ("MiniMax-H3-Image", 10.0, 25.0),
                ("MiniMax-H3-Image-Highspeed", 20.0, 50.0),
            )
        ]
        return {
            "dataset_version": "2026-11-18T00:00:00Z",
            "entries": [dump_entry(image_01), *h3_entries],
            "builtin_providers": {
                "minimax": {
                    "models": [
                        {"api_model_name": "image-01", "entry": "minimax/image-01", "endpoint": "minimax-image"},
                        *[
                            {
                                "api_model_name": e["name"],
                                "entry": f"minimax/{e['name']}",
                                "endpoint": "minimax-h3-image",
                                "min_app_version": floor,
                            }
                            for e in h3_entries
                        ],
                    ],
                    "defaults": {"image": "image-01"},
                }
            },
        }

    def test_catalog_accepts_h3_token_prices_and_gates_older_versions(self, monkeypatch: pytest.MonkeyPatch):
        from lib.backends.backend_assembly.specs import builtin_model_endpoint
        from lib.catalog.user_entries import new_entry
        from tests.catalog_support import install_catalog

        raw = self._raw_catalog()
        assert check_catalog(raw) == []
        data = parse_catalog(raw)

        # 低于 0.37.1 时 H3 两款不进可用清单，只出现在 version_gated_models，image-01 照常可用
        install_catalog(monkeypatch, data, app_version="0.37.0")
        assert set(catalog.list_models("minimax")) == {"image-01"}
        assert builtin_model_endpoint("minimax", "image", "image-01") == "minimax-image"
        gated = {
            view.model_id: (view.min_app_version, view.min_app_version_satisfied)
            for view in catalog.version_gated_models("minimax")
        }
        assert gated == {
            "MiniMax-H3-Image": ("0.37.1", False),
            "MiniMax-H3-Image-Highspeed": ("0.37.1", False),
        }

        # 达到 0.37.1 后两款启用并绑定到 minimax-h3-image，默认模型仍是 image-01
        install_catalog(monkeypatch, parse_catalog(raw), app_version="0.37.1")
        assert set(catalog.list_models("minimax")) == {"image-01", "MiniMax-H3-Image", "MiniMax-H3-Image-Highspeed"}
        assert catalog.default_model("minimax", "image") == "image-01"
        for model in _MODELS:
            assert builtin_model_endpoint("minimax", "image", model) == "minimax-h3-image"

        # 用户在界面新建按输出图片 token 计价的条目（无 output_token_bases）同样通过校验
        created = new_entry(
            maker="minimax",
            name="MiniMax-H3-Image-Custom",
            media_type="image",
            facts={"text_to_image": True, "image_to_image": True, "resolutions": ["1K", "2K", "4K"]},
            price=raw["entries"][1]["price"],
        )
        assert created.price is not None
        assert created.price.is_priceable
