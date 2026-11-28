"""KlingModelPathImageBackend 单元测试（respx 拦截全部出站 HTTP，不调用真实供应商）。

覆盖可灵新版图像 API（``POST /image-generation/<model>`` + ``GET /tasks?task_ids=``）的：
- Bearer 鉴权与 base_url 版本段剥离（拒绝 JWT / 缺失 API Key）
- 文生图（无参考图）与多图参考生图（1–10 张有序编码、唯一 id、纯 base64）
- 官方约束校验（2K/4K/8K 分辨率、9 种比例含 auto、参考图上限 10 与缺失/不可读均在提交前拒绝）
- 提交 → 异步轮询 → 过滤 outputs[type=image] 无水印 URL → 产物下载（含下载重试不重复提交）
- 失败路径（任务 failed、提交 code 非 0、4xx 不重试、成功但缺图报 ImageOutputMissingError）
- 预估与结算计量一致，且与官方国内 CNY 与国际 USD 两套价目表对齐（不套用汇率）
- 内置装配（含两栖别名 kling-4.0-image → kling-4.0）、自定义供应商（含路径覆盖）与目录校验
"""

from __future__ import annotations

import base64
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from arcreel_catalog_core import (
    check_catalog,
    endpoint_version_floor,
    parse_catalog,
)
from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import get_provider_spec
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageOutputMissingError,
    ImageRequestFacts,
    ReferenceImage,
)
from lib.backends.image_backends.kling_model_path import KlingModelPathImageBackend
from lib.backends.image_backends.registry import create_backend
from lib.backends.image_capabilities import gate_image_request
from lib.backends.providers import PROVIDER_KLING
from lib.billing.metering import Priced, Unpriced, UnpricedReason, price_usage
from lib.config.registry import PROVIDER_REGISTRY
from lib.custom_provider.backends import CustomImageBackend
from lib.custom_provider.endpoints import endpoint_reference_image_limit, get_endpoint_spec
from lib.custom_provider.metering import custom_image_estimate
from lib.video_runtime.builtin import build_builtin_backend
from tests.custom_backend_support import create_custom_backend
from tests.factories import image_bytes
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, only_request, request_json

_BASE = "https://api-beijing.klingai.com"
_ENDPOINT = "kling-image-model-path"
_DOWNLOAD_URL = "https://cdn.test/final.png"
_IMAGE_BYTES = b"\x89PNG\r\n\x1a\nkling4-image"


def _submitted(task_id: str = "img-task-1") -> httpx.Response:
    return httpx.Response(
        200,
        json={"code": 0, "message": "SUCCEED", "request_id": "req-1", "data": {"id": task_id, "status": "submitted"}},
    )


def _task(status: str, *, task_id: str = "img-task-1", message: str = "", **extra) -> httpx.Response:
    task: dict = {"id": task_id, "status": status, "message": message, **extra}
    return httpx.Response(200, json={"code": 0, "message": "SUCCEED", "request_id": "req-q", "data": [task]})


def _succeeded(
    urls: list[str] | None = None, *, task_id: str = "img-task-1", outputs: list[dict] | None = None
) -> httpx.Response:
    items = (
        outputs
        if outputs is not None
        else [
            {"type": "image", "url": url, "watermark_url": f"{url}?wm=1"}
            for url in (urls if urls is not None else [_DOWNLOAD_URL])
        ]
    )
    return _task("succeeded", task_id=task_id, outputs=items)


def _backend(model: str = "kling-4.0", **kwargs) -> KlingModelPathImageBackend:
    return KlingModelPathImageBackend(api_key="static-key", model=model, **kwargs)


def _request(tmp_path: Path, **overrides) -> ImageGenerationRequest:
    kwargs: dict = {
        "prompt": "a film still in natural daylight",
        "output_path": tmp_path / "out.png",
        "aspect_ratio": "16:9",
        "image_size": "2K",
    }
    kwargs.update(overrides)
    return ImageGenerationRequest(**kwargs)


def _refs(tmp_path: Path, count: int) -> list[ReferenceImage]:
    refs = []
    for index in range(count):
        path = tmp_path / f"ref-{index + 1}.png"
        path.write_bytes(image_bytes(index + 1))
        refs.append(ReferenceImage(path=str(path)))
    return refs


@contextmanager
def _routes(base: str = _BASE, *, model: str = "kling-4.0") -> Generator[tuple[respx.Route, respx.Route, respx.Route]]:
    with capture_http() as router:
        submit = router.post(f"{base}/image-generation/{model}")
        poll = router.get(f"{base}/tasks")
        download = router.get(_DOWNLOAD_URL)
        yield submit, poll, download


class TestConstructionAndCapabilities:
    def test_defaults_and_capabilities(self):
        backend = _backend()
        assert backend.name == PROVIDER_KLING
        assert backend.model == "kling-4.0"
        assert backend._base_url == _BASE
        assert backend.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
        assert KlingModelPathImageBackend.CHANNEL_REFERENCE_LIMIT == 10
        assert KlingModelPathImageBackend.CHANNEL_REFERENCE_LIMIT_HARD is True
        assert endpoint_reference_image_limit(get_endpoint_spec(_ENDPOINT)) == 10

    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            ("https://api-beijing.klingai.com/v1", "https://api-beijing.klingai.com"),
            ("https://api-singapore.klingai.com/v1/", "https://api-singapore.klingai.com"),
            ("relay.test", "https://relay.test"),
            ("https://relay.test/kling", "https://relay.test/kling"),
        ],
    )
    def test_base_url_strips_trailing_version(self, configured: str, expected: str):
        assert _backend(base_url=configured)._base_url == expected

    def test_access_key_secret_key_is_rejected(self):
        with pytest.raises(ValueError, match="只支持 API Key"):
            KlingModelPathImageBackend(auth_mode="jwt", access_key="ak", secret_key="sk")

    def test_missing_api_key_is_rejected(self):
        with pytest.raises(ValueError, match="API Key"):
            KlingModelPathImageBackend(api_key=None)

    def test_registry_creates_this_backend(self):
        backend = create_backend(_ENDPOINT, auth_mode="bearer", api_key="k", model="kling-4.0")
        assert isinstance(backend, KlingModelPathImageBackend)
        assert backend.model == "kling-4.0"


class TestPayloadAndValidation:
    def test_text_to_image_payload_uses_contents_and_single_settings(self, tmp_path: Path):
        path, payload = _backend()._build_payload(_request(tmp_path, image_size="4K", aspect_ratio="3:4"))
        assert path == "image-generation/kling-4.0"
        assert payload == {
            "contents": [{"type": "prompt", "text": "a film still in natural daylight"}],
            "settings": {
                "result_type": "single",
                "n": 1,
                "resolution": "4k",
                "aspect_ratio": "3:4",
            },
        }

    def test_amphibious_alias_sends_api_model_name_in_path(self, tmp_path: Path):
        backend = _backend(model="kling-4.0-image", api_model_name="kling-4.0")
        path, _payload = backend._build_payload(_request(tmp_path))
        assert backend.model == "kling-4.0-image"
        assert path == "image-generation/kling-4.0"

    @pytest.mark.parametrize(
        ("image_size", "expected"),
        [(None, "2k"), ("", "2k"), ("2K", "2k"), ("2k", "2k"), ("4K", "4k"), ("8K", "8k"), ("8k", "8k")],
    )
    def test_supported_resolutions_normalize_to_lowercase(self, tmp_path: Path, image_size: str | None, expected: str):
        _path, payload = _backend()._build_payload(_request(tmp_path, image_size=image_size))
        assert payload["settings"]["resolution"] == expected

    @pytest.mark.parametrize("bad_size", ["1K", "1k", "512px", "3K", "1024x1024", "1080p"])
    def test_unsupported_resolution_is_rejected_before_submission(self, tmp_path: Path, bad_size: str):
        with pytest.raises(ImageCapabilityError) as caught:
            _backend()._build_payload(_request(tmp_path, image_size=bad_size))
        assert caught.value.code == "image_resolution_not_supported"
        assert caught.value.params["resolution"] == bad_size
        assert caught.value.params["supported"] == "2K, 4K, 8K"

    @pytest.mark.parametrize(
        ("aspect_ratio", "expected"),
        [
            ("16:9", "16:9"),
            ("9:16", "9:16"),
            ("1:1", "1:1"),
            ("4:3", "4:3"),
            ("3:4", "3:4"),
            ("3:2", "3:2"),
            ("2:3", "2:3"),
            ("21:9", "21:9"),
            ("auto", "auto"),
            ("AUTO", "auto"),
            ("", "auto"),
        ],
    )
    def test_supported_aspect_ratios(self, tmp_path: Path, aspect_ratio: str, expected: str):
        _path, payload = _backend()._build_payload(_request(tmp_path, aspect_ratio=aspect_ratio))
        assert payload["settings"]["aspect_ratio"] == expected

    @pytest.mark.parametrize("bad_ratio", ["4:5", "5:4", "1:4", "10:1", "invalid"])
    def test_unsupported_aspect_ratio_is_rejected_before_submission(self, tmp_path: Path, bad_ratio: str):
        with pytest.raises(ImageCapabilityError) as caught:
            _backend()._build_payload(_request(tmp_path, aspect_ratio=bad_ratio))
        assert caught.value.code == "image_aspect_ratio_not_supported"
        assert caught.value.params["aspect_ratio"] == bad_ratio

    def test_multi_reference_preserves_order_unique_ids_and_pure_base64(self, tmp_path: Path):
        refs = _refs(tmp_path, 10)
        _path, payload = _backend()._build_payload(_request(tmp_path, reference_images=refs, image_size="8K"))
        contents = payload["contents"]
        assert len(contents) == 11
        assert contents[0] == {"type": "prompt", "text": "a film still in natural daylight"}
        ref_items = contents[1:]
        assert [item["type"] for item in ref_items] == ["refer_image"] * 10
        assert len({item["id"] for item in ref_items}) == 10
        assert [item["url"] for item in ref_items] == [
            base64.b64encode(Path(ref.path).read_bytes()).decode("ascii") for ref in refs
        ]
        assert all(not item["url"].startswith("data:") for item in ref_items)
        assert payload["settings"]["resolution"] == "8k"

    async def test_eleven_references_rejected_before_http_even_with_override(self, tmp_path: Path):
        refs = _refs(tmp_path, 11)
        wrapped = CustomImageBackend(
            provider_id="custom-1",
            delegate=_backend(),
            model="kling-4.0",
            reference_limit_override=12,
        )
        await gate_image_request(wrapped, provider_id="custom-1", reference_count=11)
        with capture_http() as router, pytest.raises(ImageCapabilityError) as caught:
            await wrapped.generate(_request(tmp_path, reference_images=refs))

        assert caught.value.code == "image_reference_images_exceeded"
        assert caught.value.params["limit"] == 10
        assert caught.value.params["count"] == 11
        assert not router.calls

    def test_missing_reference_image_raises_unreadable(self, tmp_path: Path):
        missing = ReferenceImage(path=str(tmp_path / "missing.png"))
        with pytest.raises(ImageCapabilityError) as caught:
            _backend()._build_payload(_request(tmp_path, reference_images=[missing]))
        assert caught.value.code == "image_reference_images_unreadable"

    def test_safe_log_view_omits_prompt_and_base64(self, tmp_path: Path):
        backend = _backend()
        path, payload = backend._build_payload(_request(tmp_path, reference_images=_refs(tmp_path, 2)))
        view = backend._safe_log_view(path, payload)
        assert view == {
            "endpoint": "image-generation/kling-4.0",
            "result_type": "single",
            "n": 1,
            "resolution": "2k",
            "aspect_ratio": "16:9",
            "reference_count": 2,
            "prompt_len": len("a film still in natural daylight"),
        }
        assert "contents" not in view
        assert "prompt" not in view


class TestGenerateFlow:
    async def test_text_to_image_submit_poll_and_download(self, tmp_path: Path):
        with _routes() as (submit, poll, download), bounded_poll_clock():
            submit.mock(return_value=_submitted("task-40"))
            poll.mock(
                side_effect=[
                    _task("submitted", task_id="task-40"),
                    _task("processing", task_id="task-40"),
                    _succeeded(task_id="task-40"),
                ]
            )
            download.mock(return_value=httpx.Response(200, content=_IMAGE_BYTES))

            result = await _backend().generate(_request(tmp_path, image_size="4K"))

        sent = only_request(submit)
        assert sent.headers["Authorization"] == "Bearer static-key"
        assert request_json(sent)["settings"] == {
            "result_type": "single",
            "n": 1,
            "resolution": "4k",
            "aspect_ratio": "16:9",
        }
        assert poll.call_count == 3
        assert dict(poll.calls[0].request.url.params) == {"task_ids": "task-40"}
        assert result.provider == PROVIDER_KLING
        assert result.model == "kling-4.0"
        assert result.image_uri == _DOWNLOAD_URL
        assert result.image_path.read_bytes() == _IMAGE_BYTES
        assert result.usage is not None
        assert result.usage.quantities == {"output_images": 1, "input_images": 0}
        assert result.usage.dimensions == {"resolution": "4K"}

    async def test_multi_reference_and_outputs_filtering_takes_first_non_watermark_image(self, tmp_path: Path):
        refs = _refs(tmp_path, 3)
        with _routes() as (submit, poll, download):
            submit.mock(return_value=_submitted("task-multi"))
            poll.mock(
                return_value=_succeeded(
                    task_id="task-multi",
                    outputs=[
                        {"type": "audio", "mp3_url": "https://cdn.test/a.mp3"},
                        {"type": "image", "url": "", "watermark_url": "https://cdn.test/wm-only.png"},
                        {"type": "image", "url": _DOWNLOAD_URL, "watermark_url": "https://cdn.test/wm.png"},
                        {"type": "image", "url": "https://cdn.test/second.png"},
                    ],
                )
            )
            download.mock(return_value=httpx.Response(200, content=_IMAGE_BYTES))

            result = await _backend().generate(_request(tmp_path, reference_images=refs, image_size="8K"))

        assert len(request_json(only_request(submit))["contents"]) == 4
        assert str(only_request(download).url) == _DOWNLOAD_URL
        assert result.image_uri == _DOWNLOAD_URL
        assert result.usage is not None
        assert result.usage.quantities == {"output_images": 1, "input_images": 3}
        assert result.usage.dimensions == {"resolution": "8K"}

    async def test_download_transient_failure_retries_without_resubmitting(self, tmp_path: Path):
        with _routes() as (submit, poll, download), bounded_poll_clock():
            submit.mock(return_value=_submitted("task-dl"))
            poll.mock(return_value=_succeeded(task_id="task-dl"))
            download.mock(
                side_effect=[
                    httpx.Response(404, text="not ready"),
                    httpx.Response(200, content=_IMAGE_BYTES),
                ]
            )

            result = await _backend().generate(_request(tmp_path))

        assert submit.call_count == 1
        assert download.call_count == 2
        assert result.image_path.read_bytes() == _IMAGE_BYTES

    async def test_failed_task_raises_with_upstream_message_and_skips_download(self, tmp_path: Path):
        with _routes() as (submit, poll, download):
            submit.mock(return_value=_submitted("task-fail"))
            poll.mock(return_value=_task("failed", task_id="task-fail", message="risk control triggered"))

            with pytest.raises(RuntimeError, match="risk control triggered"):
                await _backend().generate(_request(tmp_path))

        assert download.call_count == 0

    async def test_succeeded_task_without_image_output_raises_missing_output(self, tmp_path: Path):
        with _routes() as (submit, poll, download):
            submit.mock(return_value=_submitted("task-empty"))
            poll.mock(return_value=_succeeded(task_id="task-empty", outputs=[]))

            with pytest.raises(ImageOutputMissingError):
                await _backend().generate(_request(tmp_path))

        assert download.call_count == 0

    async def test_submit_error_envelope_raises(self, tmp_path: Path):
        with _routes() as (submit, poll, _download):
            submit.mock(
                return_value=httpx.Response(
                    200, json={"code": 1102, "message": "flagship resource pack required", "data": None}
                )
            )
            with pytest.raises(RuntimeError, match="flagship resource pack required"):
                await _backend().generate(_request(tmp_path))

        assert poll.call_count == 0

    async def test_http_4xx_does_not_retry(self, tmp_path: Path):
        with _routes() as (submit, _poll, _download), bounded_poll_clock():
            submit.mock(return_value=httpx.Response(400, json={"code": 1201, "message": "invalid parameter"}))
            with pytest.raises(httpx.HTTPStatusError):
                await _backend().generate(_request(tmp_path))

        assert submit.call_count == 1

    async def test_invalid_input_never_reaches_upstream(self, tmp_path: Path):
        with _routes() as (submit, _poll, _download), pytest.raises(ImageCapabilityError):
            await _backend().generate(_request(tmp_path, image_size="1K"))
        assert submit.call_count == 0


def _kling_image_4_entry(*, currency: str, input_price: float, output_prices: dict[str, float]) -> dict:
    return {
        "maker": "kuaishou",
        "name": "kling-4.0",
        "media_type": "image",
        "display_name": "可灵图像 4.0",
        "release_date": "2026-11-17",
        "facts": {
            "text_to_image": True,
            "image_to_image": True,
            "resolutions": ["2K", "4K", "8K"],
            "max_reference_images": 10,
        },
        "price": {
            "currency": currency,
            "meters": [
                {
                    "meter": "output_images",
                    "dims": ["resolution"],
                    "rows": [{"when": {"resolution": res}, "price": price} for res, price in output_prices.items()],
                    "defaults": {"resolution": "2K"},
                },
                {
                    "meter": "input_images",
                    "dims": [],
                    "rows": [{"when": {}, "price": input_price}],
                },
            ],
        },
    }


class TestMeteringAndRegionalPricing:
    @pytest.mark.parametrize(
        ("image_size", "expected_resolution"),
        [(None, "2K"), ("2K", "2K"), ("4K", "4K"), ("8K", "8K")],
    )
    @pytest.mark.parametrize("ref_count", [0, 2, 10])
    async def test_estimate_matches_actual_generate_usage(
        self, tmp_path: Path, image_size: str | None, expected_resolution: str, ref_count: int
    ):
        refs = _refs(tmp_path, ref_count)
        with _routes() as (submit, poll, download):
            submit.mock(return_value=_submitted())
            poll.mock(return_value=_succeeded())
            download.mock(return_value=httpx.Response(200, content=_IMAGE_BYTES))
            result = await _backend().generate(_request(tmp_path, image_size=image_size, reference_images=refs))

        estimate = KlingModelPathImageBackend.estimate_usage(
            ImageRequestFacts(model="kling-4.0", aspect_ratio="16:9", image_size=image_size, reference_count=ref_count)
        )
        assert result.usage is not None
        assert estimate == result.usage
        assert estimate.quantities == {"output_images": 1, "input_images": ref_count}
        assert estimate.dimensions == {"resolution": expected_resolution}

    @pytest.mark.parametrize(
        ("currency", "input_price", "output_prices", "resolution", "refs", "expected_total"),
        [
            ("CNY", 0.02, {"2K": 0.4, "4K": 0.6, "8K": 1.0}, "2K", 0, 0.4),
            ("CNY", 0.02, {"2K": 0.4, "4K": 0.6, "8K": 1.0}, "2K", 3, 0.46),
            ("CNY", 0.02, {"2K": 0.4, "4K": 0.6, "8K": 1.0}, "4K", 2, 0.64),
            ("CNY", 0.02, {"2K": 0.4, "4K": 0.6, "8K": 1.0}, "8K", 10, 1.2),
            ("USD", 0.003, {"2K": 0.063, "4K": 0.094, "8K": 0.157}, "2K", 0, 0.063),
            ("USD", 0.003, {"2K": 0.063, "4K": 0.094, "8K": 0.157}, "2K", 3, 0.072),
            ("USD", 0.003, {"2K": 0.063, "4K": 0.094, "8K": 0.157}, "4K", 2, 0.100),
            ("USD", 0.003, {"2K": 0.063, "4K": 0.094, "8K": 0.157}, "8K", 10, 0.187),
        ],
    )
    def test_official_cny_and_usd_price_tables_match_metered_usage(
        self,
        currency: str,
        input_price: float,
        output_prices: dict[str, float],
        resolution: str,
        refs: int,
        expected_total: float,
    ):
        raw_catalog = {
            "dataset_version": "2026-11-28T00:00:00Z",
            "entries": [_kling_image_4_entry(currency=currency, input_price=input_price, output_prices=output_prices)],
            "builtin_providers": {
                "kling": {
                    "models": [
                        {
                            "api_model_name": "kling-4.0",
                            "entry": "kuaishou/kling-4.0",
                            "media_type": "image",
                            "endpoint": _ENDPOINT,
                            "min_app_version": "0.38.1",
                        }
                    ],
                    "defaults": {},
                }
            },
        }
        assert check_catalog(raw_catalog) == []
        parsed = parse_catalog(raw_catalog)
        entry = parsed.entries[0]
        assert endpoint_version_floor("kling", "image", _ENDPOINT) == "0.38.1"

        usage = KlingModelPathImageBackend.estimate_usage(
            ImageRequestFacts(model="kling-4.0", aspect_ratio="16:9", image_size=resolution, reference_count=refs)
        )
        cost = price_usage(entry.price, usage, multiplier=1.0, estimate=True)
        assert isinstance(cost, Priced)
        assert cost.currency == currency
        assert cost.amount == pytest.approx(expected_total)

    def test_unsupported_resolution_is_unpriced_for_both_builtin_and_custom_estimates(self):
        entry = parse_catalog(
            {
                "dataset_version": "2026-11-28T00:00:00Z",
                "entries": [
                    _kling_image_4_entry(
                        currency="CNY", input_price=0.02, output_prices={"2K": 0.4, "4K": 0.6, "8K": 1.0}
                    )
                ],
                "builtin_providers": {},
            }
        ).entries[0]
        facts = ImageRequestFacts(model="kling-4.0", aspect_ratio="16:9", image_size="1K", reference_count=1)

        builtin_cost = price_usage(
            entry.price, KlingModelPathImageBackend.estimate_usage(facts), multiplier=1.0, estimate=True
        )
        custom_cost = price_usage(
            entry.price, custom_image_estimate(KlingModelPathImageBackend, facts), multiplier=1.0, estimate=True
        )
        assert builtin_cost == Unpriced(UnpricedReason.NO_USAGE_ESTIMATE)
        assert custom_cost == Unpriced(UnpricedReason.MISSING_RATE)

    def test_unknown_reference_count_yields_no_usage_estimate(self):
        entry = parse_catalog(
            {
                "dataset_version": "2026-11-28T00:00:00Z",
                "entries": [
                    _kling_image_4_entry(
                        currency="CNY", input_price=0.02, output_prices={"2K": 0.4, "4K": 0.6, "8K": 1.0}
                    )
                ],
                "builtin_providers": {},
            }
        ).entries[0]
        usage = KlingModelPathImageBackend.estimate_usage(
            ImageRequestFacts(model="kling-4.0", aspect_ratio="16:9", image_size="2K", reference_count=None)
        )
        assert price_usage(entry.price, usage, multiplier=1.0, estimate=True) == Unpriced(
            UnpricedReason.NO_USAGE_ESTIMATE
        )


def _custom_provider(base_url: str) -> MagicMock:
    provider = MagicMock()
    provider.base_url = base_url
    provider.api_key = "sk-relay"
    provider.provider_id = "custom-42"
    provider.id = 42
    return provider


class TestAssemblyAndCustomProvider:
    def test_builtin_assembly_builds_bearer_backend_and_rejects_jwt(self):
        spec = get_provider_spec("kling", "image", _ENDPOINT)
        backend = build_builtin_backend(
            spec,
            LoadedConfig(
                credentials={"api_key": "sk-api-1"}, provider_meta=PROVIDER_REGISTRY["kling"], rate_limiter=None
            ),
            "kling-4.0",
        )
        assert isinstance(backend, KlingModelPathImageBackend)
        assert backend._base_url == _BASE

        with pytest.raises(ValueError, match="只支持 API Key"):
            build_builtin_backend(
                spec,
                LoadedConfig(
                    credentials={"access_key": "ak-1", "secret_key": "sk-1"},
                    provider_meta=PROVIDER_REGISTRY["kling"],
                    rate_limiter=None,
                ),
                "kling-4.0",
            )

    async def test_custom_provider_strips_v1_and_supports_international_host(self, tmp_path: Path):
        backend = create_custom_backend(
            provider=_custom_provider("https://api-singapore.klingai.com/v1"),
            model_id="kling-4.0",
            endpoint=_ENDPOINT,
        )
        with _routes("https://api-singapore.klingai.com") as (submit, poll, download):
            submit.mock(return_value=_submitted("t-sg"))
            poll.mock(return_value=_succeeded(task_id="t-sg"))
            download.mock(return_value=httpx.Response(200, content=_IMAGE_BYTES))

            result = await backend.generate(_request(tmp_path))

        assert only_request(submit).headers["Authorization"] == "Bearer sk-relay"
        assert dict(only_request(poll).url.params) == {"task_ids": "t-sg"}
        assert result.image_path.read_bytes() == _IMAGE_BYTES

    async def test_custom_provider_path_overrides_rewrite_submit_and_poll(self, tmp_path: Path):
        backend = create_custom_backend(
            provider=_custom_provider("https://relay.test/kling"),
            model_id="kling-4.0",
            endpoint=_ENDPOINT,
            path_overrides={"submit": "/v2/img/{{ model }}", "poll": "/v2/tasks"},
        )
        with capture_http() as router:
            submit = router.post("https://relay.test/kling/v2/img/kling-4.0").mock(return_value=_submitted("t-1"))
            poll = router.get("https://relay.test/kling/v2/tasks").mock(return_value=_succeeded(task_id="t-1"))
            router.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=_IMAGE_BYTES))

            await backend.generate(_request(tmp_path))

        assert request_json(only_request(submit))["settings"]["result_type"] == "single"
        assert dict(only_request(poll).url.params) == {"task_ids": "t-1"}
