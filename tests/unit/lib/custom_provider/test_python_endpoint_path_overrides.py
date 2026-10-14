"""Python 端点的路径覆盖：经端点注册表构造调用通道，respx 捕获 transport 层改写后的真实请求。"""

from __future__ import annotations

import base64
import contextlib
from pathlib import Path
from types import SimpleNamespace

import httpx

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.image_backends.base import ImageGenerationRequest, ReferenceImage
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http, only_request

_PROVIDER = SimpleNamespace(provider_id="custom-7", api_key="sk", base_url="https://relay.example/proxy")


async def test_async_video_overriding_only_poll_keeps_submit_on_the_default_path(tmp_path: Path):
    backend = ENDPOINT_REGISTRY["dashscope-async-video"].build_backend(
        _PROVIDER, "happyhorse-1.0-t2v", {"poll": "/relay/tasks/{{ task_id }}"}
    )
    with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
        submit = router.post("https://relay.example/proxy/api/v1/services/aigc/video-generation/video-synthesis").mock(
            return_value=httpx.Response(200, json={"output": {"task_id": "t-1", "task_status": "PENDING"}})
        )
        poll = router.get("https://relay.example/proxy/relay/tasks/t-1").mock(
            return_value=httpx.Response(
                200, json={"output": {"task_status": "SUCCEEDED", "video_url": "https://cdn.example/tasks/r.mp4"}}
            )
        )
        # 取自响应的下载地址不属于任何请求，原样发出
        download = router.get("https://cdn.example/tasks/r.mp4").mock(return_value=httpx.Response(200, content=b"v"))
        await backend.generate(
            VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p", task_id="w-1")
        )

    only_request(submit)
    only_request(poll)
    only_request(download)


async def test_sdk_endpoint_override_is_relative_to_the_raw_base_url_and_expands_the_model():
    # 方舟 SDK 走同步 httpx 客户端；有覆盖的提交不再补 /api/v3
    backend = ENDPOINT_REGISTRY["ark-seedance"].build_backend(
        _PROVIDER, "seedance-x", {"submit": "/ark/{{ model }}/tasks?async=1"}
    )
    with capture_http() as router, bounded_poll_clock():
        submit = router.post("https://relay.example/proxy/ark/seedance-x/tasks", params={"async": "1"}).mock(
            return_value=httpx.Response(400, json={"error": {"message": "stop here"}})
        )
        with contextlib.suppress(Exception):
            await backend.generate(VideoGenerationRequest(prompt="p", output_path=Path("unused.mp4")))
        # 400 会被重试；未声明路由的请求会抛错并被吞掉，所以比对总请求数
        assert submit.called
        assert router.calls.call_count == submit.call_count


async def test_openai_images_overriding_only_edit_skips_v1_and_keeps_generate_normalized(tmp_path: Path):
    backend = ENDPOINT_REGISTRY["openai-images"].build_backend(
        _PROVIDER, "gpt-image-1", {"edit": "/img/{{ model }}/edit"}
    )
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\nfake")
    image = httpx.Response(200, json={"created": 1, "data": [{"b64_json": base64.b64encode(b"png").decode()}]})

    with capture_http() as router:
        edit = router.post("https://relay.example/proxy/img/gpt-image-1/edit").mock(return_value=image)
        generate = router.post("https://relay.example/proxy/v1/images/generations").mock(return_value=image)
        await backend.generate(
            ImageGenerationRequest(
                prompt="猫", output_path=tmp_path / "edit.png", reference_images=[ReferenceImage(path=str(ref))]
            )
        )
        await backend.generate(ImageGenerationRequest(prompt="猫", output_path=tmp_path / "generate.png"))

    assert edit.call_count == 1
    assert generate.call_count == 1
