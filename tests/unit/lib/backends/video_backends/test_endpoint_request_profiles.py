"""Endpoints fix request shape and metering; API model names do not select the request shape."""

import json

import httpx
import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.video_backends.kling import KLING_BACKENDS
from lib.backends.video_backends.vidu import VIDU_BACKENDS
from tests.http_capture import capture_http


@pytest.mark.parametrize(
    ("endpoint", "route", "seconds", "audio"),
    [
        ("vidu-video-generic", "text2video", 12, False),
        ("vidu-video-q3-pro", "text2video", 12, True),
        ("vidu-video-q3-turbo", "reference2video", 12, True),
        ("vidu-video-q3-fast", "img2video", 12, True),
        ("vidu-video-q3-reference", "reference2video", 12, True),
        ("vidu-video-q2", "text2video", 10, False),
        ("vidu-video-q2-pro", "start-end2video", 10, False),
        ("vidu-video-q2-fast", "start-end2video", 10, False),
        ("vidu-video-q2-fast", "img2video", 10, False),
        ("vidu-video-q1", "text2video", 5, False),
        ("vidu-video-q1-classic", "img2video", 5, False),
        ("vidu-video-2", "img2video", 8, False),
        ("vidu-video-q4", "reference2video", 12, True),
    ],
)
async def test_vidu_endpoint_request_and_metering_ignore_name(endpoint, route, seconds, audio, tmp_path):
    image = tmp_path / "in.png"
    image.write_bytes(b"image")
    fields = {}
    if route == "reference2video":
        fields["reference_images"] = [image]
    elif route in ("img2video", "start-end2video"):
        fields["start_image"] = image
        if route == "start-end2video":
            fields["end_image"] = image
    request = VideoGenerationRequest(prompt="test", output_path=tmp_path / "out.mp4", duration_seconds=12, **fields)
    for model in ("opaque-id", "viduq3-pro"):
        backend = VIDU_BACKENDS[endpoint](api_key="test", model=model, base_url="https://vidu.test/ent/v2")
        with capture_http() as router:
            submit = router.post(f"https://vidu.test/ent/v2/{route}").mock(
                return_value=httpx.Response(200, json={"task_id": "job"})
            )
            router.get("https://vidu.test/ent/v2/tasks/job/creations").mock(
                return_value=httpx.Response(
                    200, json={"state": "success", "creations": [{"url": "https://cdn.test/v.mp4"}]}
                )
            )
            router.get("https://cdn.test/v.mp4").mock(return_value=httpx.Response(200, content=b"video"))
            result = await backend.generate(request)
        body = json.loads(submit.calls[0].request.content)
        assert body["model"] == model
        assert body["duration"] == seconds
        assert body.get("audio", False) is audio
        usage = backend.metered_usage(model, request)
        assert usage.quantities["output_seconds"] == seconds
        assert usage.dimensions["audio"] is audio
        assert result.duration_seconds == seconds
        assert result.generate_audio is audio


@pytest.mark.parametrize(
    ("endpoint", "audio"),
    [
        ("kling-video-pro-silent", False),
        ("kling-video-pro-audio", True),
        ("kling-video-audio", True),
        ("kling-video-silent", False),
    ],
)
async def test_kling_endpoint_request_and_metering_ignore_name(endpoint, audio, tmp_path):
    request = VideoGenerationRequest(prompt="test", output_path=tmp_path / "out.mp4", service_tier="pro")
    for model in ("opaque-id", "kling-v3"):
        backend = KLING_BACKENDS[endpoint](
            auth_mode="bearer", api_key="test", model=model, base_url="https://kling.test/v1"
        )
        with capture_http() as router:
            submit = router.post("https://kling.test/v1/videos/text2video").mock(
                return_value=httpx.Response(200, json={"code": 0, "data": {"task_id": "job"}})
            )
            router.get("https://kling.test/v1/videos/text2video/job").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "task_status": "succeed",
                            "task_result": {"videos": [{"url": "https://cdn.test/v.mp4"}]},
                        },
                    },
                )
            )
            router.get("https://cdn.test/v.mp4").mock(return_value=httpx.Response(200, content=b"video"))
            result = await backend.generate(request)
        body = json.loads(submit.calls[0].request.content)
        assert body["model_name"] == model
        assert body["mode"] == "pro"
        assert body.get("sound", "off") == ("on" if audio else "off")
        usage = backend.metered_usage(model, request)
        assert usage.dimensions["audio"] is audio
        assert usage.dimensions["resolution"] == "1080p"
        assert result.generate_audio is audio
