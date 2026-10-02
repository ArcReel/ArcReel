from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
import pytest

from lib.backends.image_backends.base import ImageCapability, ImageGenerationRequest, ReferenceImage
from lib.custom_provider.declarative_backend import DeclarativeRuntimeError
from lib.custom_provider.declarative_image_backend import DeclarativeImageBackend
from tests.factories import image_endpoint_definition
from tests.fakes import PNG_BYTES, bounded_poll_clock
from tests.http_capture import capture_http, only_request, request_json


def _backend(definition: dict[str, Any] | None = None) -> DeclarativeImageBackend:
    return DeclarativeImageBackend(
        api_key="secret",
        base_url="https://relay.test",
        model="gpt-image-2",
        definition=definition or image_endpoint_definition(),
        provider="custom-1",
    )


def _image_to_image_definition(*, encoding: str = "data_uri", max_reference_images: int = 2) -> dict[str, Any]:
    """同时声明文生图与图生图、参考图可选的定义：参考图逐张铺进 ``image_urls``。"""
    definition = image_endpoint_definition(
        capabilities={"text_to_image": True, "image_to_image": True, "max_reference_images": max_reference_images},
        inputs={"refs": {"source": "reference_images", "encoding": encoding}},
    )
    definition["submit"]["body"]["image_urls"] = [
        {"$each": {"in": "inputs.refs", "as": "image", "item": "{{ image }}"}}
    ]
    return definition


def _reference_files(tmp_path: Path, count: int) -> list[ReferenceImage]:
    references = []
    for index in range(count):
        path = tmp_path / f"ref{index}.png"
        path.write_bytes(PNG_BYTES + bytes([index]))
        references.append(ReferenceImage(path=str(path)))
    return references


def _completed() -> httpx.Response:
    return _task("completed", result={"images": [{"url": ["https://cdn.test/img/first.png"]}]})


def _url_then_base64_definition() -> dict[str, Any]:
    definition = image_endpoint_definition()
    definition["poll"]["extract"]["image_b64"] = ["$.data.result.images[0].b64_json"]
    return definition


def _base64_only_definition() -> dict[str, Any]:
    definition = _url_then_base64_definition()
    del definition["poll"]["extract"]["image_url"]
    return definition


PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")


def _request(tmp_path: Path, **overrides) -> ImageGenerationRequest:
    values = {
        "prompt": "a lighthouse at dusk",
        "output_path": tmp_path / "out.png",
        "aspect_ratio": "1:1",
        "image_size": "1K",
        "seed": 7,
    }
    values.update(overrides)
    return ImageGenerationRequest(**values)


def _submitted() -> httpx.Response:
    return httpx.Response(200, json={"code": 200, "data": [{"status": "submitted", "task_id": "task_9"}]})


def _task(status: str, **data: object) -> httpx.Response:
    return httpx.Response(200, json={"code": 200, "data": {"status": status, **data}})


class TestDeclarativeImageBackend:
    def test_text_to_image_is_the_declared_capability(self):
        assert _backend().capabilities == {ImageCapability.TEXT_TO_IMAGE}

    def test_declared_image_to_image_and_reference_limit_are_the_backend_capabilities(self):
        backend = _backend(_image_to_image_definition(max_reference_images=3))

        assert backend.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
        assert backend.max_reference_images == 3

    @pytest.mark.parametrize(
        ("encoding", "prefix"),
        [("data_uri", "data:image/png;base64,"), ("base64", "")],
    )
    async def test_reference_images_enter_the_body_in_the_declared_encoding_up_to_the_limit(
        self, tmp_path: Path, encoding: str, prefix: str
    ):
        references = _reference_files(tmp_path, 3)
        with capture_http() as router, bounded_poll_clock():
            submit = router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(return_value=_completed())
            router.get("https://cdn.test/img/first.png").mock(return_value=httpx.Response(200, content=PNG_BYTES))

            await _backend(_image_to_image_definition(encoding=encoding, max_reference_images=2)).generate(
                _request(tmp_path, prompt="把头发改成红色", reference_images=references)
            )

        body = request_json(only_request(submit))
        assert body["prompt"] == "把头发改成红色"
        assert body["image_urls"] == [
            prefix + base64.b64encode(PNG_BYTES + bytes([index])).decode() for index in range(2)
        ]

    async def test_text_to_image_on_a_definition_with_optional_references_sends_no_images(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            submit = router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(return_value=_completed())
            router.get("https://cdn.test/img/first.png").mock(return_value=httpx.Response(200, content=PNG_BYTES))

            await _backend(_image_to_image_definition()).generate(_request(tmp_path))

        assert "image_urls" not in request_json(only_request(submit))

    async def test_definition_drives_submit_poll_and_image_download(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            submit = router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(
                side_effect=[
                    _task("pending"),
                    _task("processing"),
                    _task(
                        "completed",
                        result={
                            "images": [
                                {"url": ["https://cdn.test/img/first.png"]},
                                {"url": ["https://cdn.test/img/second.png"]},
                            ]
                        },
                    ),
                ]
            )
            router.get("https://cdn.test/img/first.png").mock(return_value=httpx.Response(200, content=PNG_BYTES))

            result = await _backend().generate(_request(tmp_path))

        sent = only_request(submit)
        assert sent.headers["Authorization"] == "Bearer secret"
        assert request_json(sent) == {
            "model": "gpt-image-2",
            "prompt": "a lighthouse at dusk",
            "size": "1024x1024",
            "seed": 7,
        }
        assert result.image_path == tmp_path / "out.png"
        assert result.image_path.read_bytes() == PNG_BYTES
        assert (result.provider, result.model) == ("custom-1", "gpt-image-2")
        assert result.image_uri == "https://cdn.test/img/first.png"

    async def test_provider_failure_carries_the_provider_reason(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(
                return_value=_task("failed", error={"message": "content policy violation"})
            )

            with pytest.raises(RuntimeError, match="content policy violation"):
                await _backend().generate(_request(tmp_path))

    async def test_cancelled_task_without_a_reason_names_the_provider_status(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            poll = router.get("https://relay.test/v1/tasks/task_9").mock(return_value=_task("cancelled"))

            with pytest.raises(RuntimeError, match="cancelled"):
                await _backend().generate(_request(tmp_path))

        assert poll.call_count == 1

    async def test_success_without_a_matching_image_url_fails_with_a_stable_code(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(return_value=_task("completed", result={}))

            with pytest.raises(DeclarativeRuntimeError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "declarative_response_extract_failed"
        assert not (tmp_path / "out.png").exists()

    @pytest.mark.parametrize("encoded", [PNG_B64, f"data:image/png;base64,{PNG_B64}"])
    async def test_inline_base64_image_is_written_without_a_download(self, tmp_path: Path, encoded: str):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(
                return_value=_task("completed", result={"images": [{"b64_json": encoded}]})
            )

            result = await _backend(_base64_only_definition()).generate(_request(tmp_path))

        assert result.image_path.read_bytes() == PNG_BYTES
        assert result.image_uri is None

    async def test_url_wins_when_both_artifact_paths_match(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(
                return_value=_task(
                    "completed",
                    result={"images": [{"url": ["https://cdn.test/img/a.png"], "b64_json": "bm90LXRoaXM="}]},
                )
            )
            router.get("https://cdn.test/img/a.png").mock(return_value=httpx.Response(200, content=PNG_BYTES))

            result = await _backend(_url_then_base64_definition()).generate(_request(tmp_path))

        assert result.image_path.read_bytes() == PNG_BYTES
        assert result.image_uri == "https://cdn.test/img/a.png"

    async def test_base64_is_the_fallback_when_the_url_path_misses(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(
                return_value=_task("completed", result={"images": [{"b64_json": PNG_B64}]})
            )

            result = await _backend(_url_then_base64_definition()).generate(_request(tmp_path))

        assert result.image_path.read_bytes() == PNG_BYTES

    async def test_undecodable_base64_fails_with_a_stable_code(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(
                return_value=_task("completed", result={"images": [{"b64_json": "not base64!"}]})
            )

            with pytest.raises(DeclarativeRuntimeError) as caught:
                await _backend(_base64_only_definition()).generate(_request(tmp_path))

        assert caught.value.code == "declarative_response_extract_failed"
        assert not (tmp_path / "out.png").exists()

    @pytest.mark.parametrize("value", ["0123456789abcdef0123456789abcdef", "SUCCESS"])
    async def test_decodable_base64_that_is_not_an_image_fails_without_writing(self, tmp_path: Path, value: str):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(
                return_value=_task("completed", result={"images": [{"b64_json": value}]})
            )

            with pytest.raises(DeclarativeRuntimeError) as caught:
                await _backend(_base64_only_definition()).generate(_request(tmp_path))

        assert caught.value.code == "declarative_response_extract_failed"
        assert not (tmp_path / "out.png").exists()

    @pytest.mark.parametrize(
        ("image", "mime"),
        [
            (b"\xff\xd8\xff\xe0jpeg-bytes", "jpeg"),
            (b"RIFF\x00\x00\x00\x00WEBPwebp-bytes", "webp"),
        ],
    )
    @pytest.mark.parametrize("prefix", ["", "data:image/{mime};base64,"])
    async def test_inline_jpeg_and_webp_are_written(self, tmp_path: Path, image: bytes, mime: str, prefix: str):
        encoded = prefix.format(mime=mime) + base64.b64encode(image).decode("ascii")
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(
                return_value=_task("completed", result={"images": [{"b64_json": encoded}]})
            )

            result = await _backend(_base64_only_definition()).generate(_request(tmp_path))

        assert result.image_path.read_bytes() == image

    async def test_exhausted_image_download_asks_for_regeneration_not_a_download_retry(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(return_value=_completed())
            router.get("https://cdn.test/img/first.png").mock(return_value=httpx.Response(503))

            with pytest.raises(DeclarativeRuntimeError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "declarative_image_save_failed"

    async def test_inline_image_that_cannot_be_written_fails_with_a_stable_code(self, tmp_path: Path):
        (tmp_path / "blocked").write_bytes(b"")
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/images/generations").mock(return_value=_submitted())
            router.get("https://relay.test/v1/tasks/task_9").mock(
                return_value=_task("completed", result={"images": [{"b64_json": PNG_B64}]})
            )

            with pytest.raises(DeclarativeRuntimeError) as caught:
                await _backend(_base64_only_definition()).generate(
                    _request(tmp_path, output_path=tmp_path / "blocked" / "out.png")
                )

        assert caught.value.code == "declarative_image_save_failed"
