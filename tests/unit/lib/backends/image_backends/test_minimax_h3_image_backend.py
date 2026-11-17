"""MiniMaxH3ImageBackend 单元测试（respx 拦截出站 HTTP，不调用真实供应商）。

请求与响应形状取自官方文档：
https://platform.minimax.cn/docs/api-reference/image-generation-h3-generations
https://platform.minimax.cn/docs/api-reference/image-generation-h3-edits
"""

from __future__ import annotations

import base64
from collections.abc import Generator
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from arcreel_catalog_core import Meter, PriceRow, PriceTable
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
from lib.billing.metering import Unpriced, UnpricedReason, price_usage
from tests.http_capture import capture_http, only_request, request_json

_GENERATIONS = r"https://[^/]+/v1/images/generations"
_EDITS = r"https://[^/]+/v1/images/edits"
_MODELS = ("MiniMax-H3-Image", "MiniMax-H3-Image-Highspeed")
_RESULT_URL = "https://cdn.example/out.png"

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


def _multipart(request: httpx.Request) -> tuple[dict[str, str], list[tuple[str, str, str, bytes]]]:
    """拆开 multipart 请求：文本字段按 UTF-8 解码；文件部分按出现顺序给出（字段名、文件名、Content-Type、内容）。

    httpx 的文本字段不带 charset，按原始字节解 UTF-8，中文提示词才能原样比对。
    """
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content
    )
    fields: dict[str, str] = {}
    files = []
    for part in message.iter_parts():
        name = str(part.get_param("name", header="content-disposition"))
        content = part.get_payload(decode=True)
        filename = part.get_filename()
        if filename is None:
            fields[name] = content.decode("utf-8")
        else:
            files.append((name, filename, part.get_content_type(), content))
    return fields, files


@contextmanager
def _routes(response: httpx.Response | list[httpx.Response]) -> Generator[tuple[respx.Route, respx.Route, AsyncMock]]:
    """文生图与参考图编辑两条路由，外加接管结果 URL 下载的替身。"""
    download = AsyncMock()
    with capture_http() as router:
        mock = {"side_effect": response} if isinstance(response, list) else {"return_value": response}
        generations = router.post(url__regex=_GENERATIONS).mock(**mock)
        edits = router.post(url__regex=_EDITS).mock(**mock)
        with patch("lib.backends.image_backends.minimax.download_image_to_path", download):
            yield generations, edits, download


def _backend(model: str = "MiniMax-H3-Image", **kwargs) -> MiniMaxH3ImageBackend:
    return MiniMaxH3ImageBackend(api_key="sk", model=model, base_url="https://api.minimaxi.com", **kwargs)


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
        assert str(request.url) == "https://api.minimaxi.com/v1/images/generations"
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
        download.assert_awaited_once_with(_RESULT_URL, output)
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
        download.assert_not_called()

    @pytest.mark.parametrize(("suffix", "output_format"), [(".jpg", "jpeg"), (".webp", "webp"), (".png", "png")])
    async def test_output_format_follows_the_output_path(self, tmp_path: Path, suffix: str, output_format: str):
        with _routes(_image_response()) as (generations, _, _download):
            await _backend().generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / f"o{suffix}"))

        assert request_json(only_request(generations))["output_format"] == output_format

    async def test_failed_image_item_reports_missing_output(self, tmp_path: Path):
        item = {"error": {"code": 1027, "message": "content safety"}}
        with _routes(_image_response(item=item)) as (_, _, download), pytest.raises(ImageOutputMissingError):
            await _backend().generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        download.assert_not_called()


class TestReferenceEdit:
    @pytest.mark.parametrize("model", _MODELS)
    async def test_posts_multipart_to_edits_with_references_in_order(self, tmp_path: Path, model: str):
        refs = _refs(tmp_path, 3)
        jpeg = tmp_path / "late.jpg"
        jpeg.write_bytes(b"jpeg-bytes")
        refs.append(ReferenceImage(path=str(jpeg)))
        with _routes(_image_response(usage=_USAGE)) as (generations, edits, _download):
            await _backend(model).generate(
                ImageGenerationRequest(
                    prompt="图1 的人站在图4 的街口", output_path=tmp_path / "o.png", reference_images=refs
                )
            )

        request = only_request(edits)
        assert not generations.called
        assert str(request.url) == "https://api.minimaxi.com/v1/images/edits"
        assert request.headers["Authorization"] == "Bearer sk"
        assert request.headers["content-type"].startswith("multipart/form-data")
        fields, files = _multipart(request)
        assert fields == {
            "model": model,
            "prompt": "图1 的人站在图4 的街口",
            "n": "1",
            "response_format": "url",
            "output_format": "png",
            "aspect_ratio": "9:16",
            "area": "2K",
        }
        assert files == [
            ("image[]", "ref-0.png", "image/png", b"img-0"),
            ("image[]", "ref-1.png", "image/png", b"img-1"),
            ("image[]", "ref-2.png", "image/png", b"img-2"),
            ("image[]", "late.jpg", "image/jpeg", b"jpeg-bytes"),
        ]

    async def test_sixteen_references_are_all_sent(self, tmp_path: Path):
        with _routes(_image_response()) as (_, edits, _download):
            await _backend().generate(
                ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=_refs(tmp_path, 16))
            )

        assert [name for _, name, _, _ in _multipart(only_request(edits))[1]] == [f"ref-{i}.png" for i in range(16)]

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

    def test_estimate_stays_unknown_under_a_token_price_table(self):
        usage = MiniMaxH3ImageBackend.estimate_usage(
            ImageRequestFacts(model="MiniMax-H3-Image", aspect_ratio="9:16", image_size="2K", reference_count=2)
        )
        table = PriceTable(
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

        assert price_usage(table, usage, multiplier=1.0, estimate=True) == Unpriced(UnpricedReason.NO_USAGE_ESTIMATE)


class TestSubmissionIsNotRepeated:
    async def test_read_timeout_fails_without_resubmitting(self, tmp_path: Path, poll_clock):
        with _routes(_image_response()) as (generations, _, download):
            generations.side_effect = httpx.ReadTimeout("read timed out")
            with pytest.raises(AmbiguousSubmitError):
                await _backend().generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))

        assert generations.call_count == 1
        download.assert_not_called()

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
        download.assert_awaited_once()

    async def test_download_failure_is_retried_without_resubmitting(self, tmp_path: Path, poll_clock):
        from lib.backends.backend_runtime import VIDEO_POLL_MAX_CONSECUTIVE_FAILURES

        with _routes(_image_response()) as (generations, _, download):
            download.side_effect = httpx.ConnectError("conn reset")
            with pytest.raises(RuntimeError, match="conn reset"):
                await _backend().generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))

        assert generations.call_count == 1
        assert download.call_count == VIDEO_POLL_MAX_CONSECUTIVE_FAILURES


class TestCustomProviderEndpoint:
    async def test_generate_and_edit_paths_can_be_overridden_separately(self, tmp_path: Path):
        from types import SimpleNamespace

        from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

        provider = SimpleNamespace(
            id=7, provider_id="custom-7", api_key="sk", base_url="https://relay.example/v1", discovery_format="openai"
        )
        backend = ENDPOINT_REGISTRY["minimax-h3-image"].build_backend(
            provider, "MiniMax-H3-Image", {"edit": "/h3/edits"}
        )
        with capture_http() as router:
            edits = router.post("https://relay.example/v1/h3/edits").mock(return_value=_image_response())
            generations = router.post("https://relay.example/v1/images/generations").mock(
                return_value=_image_response()
            )
            with patch("lib.backends.image_backends.minimax.download_image_to_path", AsyncMock()):
                await backend.generate(
                    ImageGenerationRequest(
                        prompt="p", output_path=tmp_path / "a.png", reference_images=_refs(tmp_path, 1)
                    )
                )
                await backend.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "b.png"))

        assert edits.call_count == 1
        assert generations.call_count == 1
