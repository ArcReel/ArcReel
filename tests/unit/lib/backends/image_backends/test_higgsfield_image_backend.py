"""HiggsfieldImageBackend 单元测试 — 端点分派、参考图上传换 URL、请求体字段与轮询取件。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageOutputMissingError,
    ImageRequestFacts,
    ReferenceImage,
)
from lib.backends.image_backends.higgsfield import (
    DEFAULT_MODEL,
    MODEL_QWEN_IMAGE_3,
    MODEL_SOUL_2,
    HiggsfieldImageBackend,
    _endpoint_for,
)
from lib.backends.providers import PROVIDER_HIGGSFIELD
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, only_request, request_json

API = "https://api.higgsfield.ai"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def _backend(model: str | None = None) -> HiggsfieldImageBackend:
    return HiggsfieldImageBackend(access_key="id", secret_key="secret", model=model)


class TestBasics:
    def test_name_and_default_model(self):
        backend = _backend()
        assert backend.name == PROVIDER_HIGGSFIELD
        assert backend.model == DEFAULT_MODEL == MODEL_QWEN_IMAGE_3
        assert backend.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def test_reference_limits(self):
        assert await _backend(MODEL_QWEN_IMAGE_3).reference_image_limit() == 3
        assert await _backend(MODEL_SOUL_2).reference_image_limit() == 0

    def test_endpoint_dispatch(self):
        assert _endpoint_for(MODEL_SOUL_2, 0) == "/higgsfield-ai/soul/v2/standard"
        assert _endpoint_for(MODEL_QWEN_IMAGE_3, 0) == "/alibaba/qwen-image-3/text-to-image"
        assert _endpoint_for(MODEL_QWEN_IMAGE_3, 2) == "/alibaba/qwen-image-3/edit"
        assert _endpoint_for("vendor/model-x", 1) == "/vendor/model-x/edit"

    def test_estimate_usage_clamps_references_and_reports_resolution(self):
        usage = HiggsfieldImageBackend.estimate_usage(
            ImageRequestFacts(model=MODEL_QWEN_IMAGE_3, aspect_ratio="9:16", image_size="2k", reference_count=5)
        )
        assert usage.quantities == {"output_images": 1, "input_images": 3}
        assert usage.dimensions == {"resolution": "2k"}


class TestBuildBody:
    def test_whitelisted_aspect_ratio_and_seed_pass_through(self, tmp_path: Path):
        body = _backend(MODEL_SOUL_2)._build_body(
            ImageGenerationRequest(
                prompt="p", output_path=tmp_path / "o.png", aspect_ratio="3:2", image_size="1080p", seed=7
            ),
            [],
        )
        assert body == {"prompt": "p", "aspect_ratio": "3:2", "resolution": "1080p", "seed": 7}

    def test_out_of_whitelist_aspect_ratio_and_seed_are_dropped(self, tmp_path: Path):
        body = _backend(MODEL_SOUL_2)._build_body(
            ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", aspect_ratio="21:9", seed=0),
            [],
        )
        assert "aspect_ratio" not in body
        assert "seed" not in body


class TestGenerate:
    async def test_soul_rejects_reference_images(self, tmp_path: Path):
        ref = tmp_path / "r.png"
        ref.write_bytes(PNG)
        request = ImageGenerationRequest(
            prompt="p", output_path=tmp_path / "o.png", reference_images=[ReferenceImage(path=str(ref))]
        )
        with capture_http() as router:
            with pytest.raises(ImageCapabilityError) as exc_info:
                await _backend(MODEL_SOUL_2).generate(request)
            assert router.calls.call_count == 0
        assert exc_info.value.code == "image_capability_missing_i2i"

    async def test_edit_flow_uploads_references_then_polls_and_downloads(self, tmp_path: Path):
        ref = tmp_path / "r.png"
        ref.write_bytes(PNG)
        out = tmp_path / "o.png"
        request = ImageGenerationRequest(
            prompt="换成蓝色",
            output_path=out,
            aspect_ratio="9:16",
            image_size="1k",
            reference_images=[ReferenceImage(path=str(ref))],
        )
        with capture_http() as router, bounded_poll_clock():
            router.post(f"{API}/files/generate-upload-url").mock(
                return_value=httpx.Response(
                    200,
                    json={"public_url": "https://cdn.example.com/r.png", "upload_url": "https://storage.example.com/p"},
                )
            )
            router.put("https://storage.example.com/p").mock(return_value=httpx.Response(200))
            submit = router.post(f"{API}/alibaba/qwen-image-3/edit").mock(
                return_value=httpx.Response(200, json={"status": "queued", "request_id": "req-1"})
            )
            router.get(f"{API}/requests/req-1/status").mock(
                side_effect=[
                    httpx.Response(200, json={"status": "in_progress", "request_id": "req-1"}),
                    httpx.Response(
                        200,
                        json={
                            "status": "completed",
                            "request_id": "req-1",
                            "images": [{"url": "https://cdn.example.com/out.png"}],
                        },
                    ),
                ]
            )
            router.get("https://cdn.example.com/out.png").mock(return_value=httpx.Response(200, content=PNG))

            result = await _backend(MODEL_QWEN_IMAGE_3).generate(request)

        sent = only_request(submit)
        assert request_json(sent) == {
            "prompt": "换成蓝色",
            "image_urls": ["https://cdn.example.com/r.png"],
            "aspect_ratio": "9:16",
            "resolution": "1k",
        }
        assert sent.headers["Idempotency-Key"]
        assert sent.headers["Authorization"] == "Key id:secret"
        assert out.read_bytes() == PNG
        assert result.provider == PROVIDER_HIGGSFIELD
        assert result.model == MODEL_QWEN_IMAGE_3
        assert result.usage is not None
        assert result.usage.quantities == {"output_images": 1, "input_images": 1}
        assert result.usage.dimensions == {"resolution": "1k"}

    async def test_failed_status_raises(self, tmp_path: Path):
        request = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png")
        with capture_http() as router, bounded_poll_clock():
            router.post(f"{API}/higgsfield-ai/soul/v2/standard").mock(
                return_value=httpx.Response(200, json={"status": "queued", "request_id": "req-2"})
            )
            router.get(f"{API}/requests/req-2/status").mock(
                return_value=httpx.Response(200, json={"status": "nsfw", "request_id": "req-2"})
            )
            with pytest.raises(RuntimeError, match="nsfw"):
                await _backend(MODEL_SOUL_2).generate(request)

    async def test_completed_without_images_is_output_missing(self, tmp_path: Path):
        request = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png")
        with capture_http() as router, bounded_poll_clock():
            router.post(f"{API}/higgsfield-ai/soul/v2/standard").mock(
                return_value=httpx.Response(200, json={"status": "queued", "request_id": "req-3"})
            )
            router.get(f"{API}/requests/req-3/status").mock(
                return_value=httpx.Response(200, json={"status": "completed", "request_id": "req-3", "images": []})
            )
            with pytest.raises(ImageOutputMissingError):
                await _backend(MODEL_SOUL_2).generate(request)

    async def test_4xx_on_submit_is_not_retried(self, tmp_path: Path):
        request = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png")
        with capture_http() as router, bounded_poll_clock():
            submit = router.post(f"{API}/higgsfield-ai/soul/v2/standard").mock(
                return_value=httpx.Response(422, json={"detail": "bad"})
            )
            with pytest.raises(httpx.HTTPStatusError):
                await _backend(MODEL_SOUL_2).generate(request)
        assert submit.call_count == 1
