"""google 家族 Python 端点的路径覆盖：经 transport 层按请求名改写 google-genai SDK 发出的路径。"""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.audio_backends.base import AudioSynthesisRequest
from lib.backends.image_backends.base import ImageGenerationRequest
from lib.backends.text_backends.base import TextGenerationRequest
from lib.custom_provider.endpoints import get_endpoint_spec
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http

BASE = "https://relay.example/google/v1"
PROVIDER = cast("Any", SimpleNamespace(provider_id="custom-g", base_url=BASE, api_key="k"))
OPERATION = "models/veo-x/operations/op1"


def _stop_here() -> httpx.Response:
    return httpx.Response(400, json={"error": {"code": 400, "message": "stop here", "status": "INVALID_ARGUMENT"}})


async def _generate(endpoint: str, model: str, overrides: dict[str, str], tmp_path: Path) -> list[str]:
    """按端点发一次生成，回包一律拒绝，只返回发出的请求路径。"""
    backend: Any = get_endpoint_spec(endpoint).build_backend(PROVIDER, model, overrides)
    call = {
        "gemini-generate": lambda: backend.generate(TextGenerationRequest(prompt="猫")),
        "gemini-image": lambda: backend.generate(ImageGenerationRequest(prompt="猫", output_path=tmp_path / "o.png")),
        "gemini-tts": lambda: backend.synthesize(
            AudioSynthesisRequest(text="你好", output_path=tmp_path / "o.wav", voice="Kore")
        ),
    }[endpoint]
    with capture_http() as router:
        route = router.route(host="relay.example").mock(return_value=_stop_here())
        with contextlib.suppress(Exception):
            await call()
    return [call.request.url.path for call in route.calls]


@pytest.mark.parametrize("endpoint", ["gemini-generate", "gemini-image", "gemini-tts"])
async def test_overridden_generate_keeps_version_segment_of_raw_base_url(endpoint: str, tmp_path: Path):
    paths = await _generate(endpoint, "gemini-x", {"generate": "/api/{{ model }}/gen"}, tmp_path)

    assert paths
    assert set(paths) == {"/google/v1/api/gemini-x/gen"}


@pytest.mark.parametrize("endpoint", ["gemini-generate", "gemini-image", "gemini-tts", "gemini-video"])
def test_requests_declare_google_strip_version_rule(endpoint: str):
    requests = get_endpoint_spec(endpoint).requests

    assert requests
    assert {r.base_url_rule for r in requests} == {"strip_version"}
    assert all(r.default_path.startswith("/v1beta/") for r in requests)


class TestGeminiVideo:
    async def _run(self, overrides: dict[str, str], tmp_path: Path) -> list[str]:
        backend: Any = get_endpoint_spec("gemini-video").build_backend(PROVIDER, "veo-x", overrides)
        request = VideoGenerationRequest(prompt="猫", output_path=tmp_path / "o.mp4", duration_seconds=5)
        done = {
            "name": OPERATION,
            "done": True,
            "response": {
                "generateVideoResponse": {
                    "generatedSamples": [{"video": {"uri": "https://cdn.example/v1beta/files/abc123:download"}}]
                }
            },
        }
        with capture_http() as router, bounded_poll_clock():
            route = router.route(host="relay.example")
            route.side_effect = lambda req: (
                httpx.Response(200, json={"name": OPERATION, "done": False})
                if req.method == "POST"
                else httpx.Response(200, json=done)
                if "abc123" not in req.url.path
                else httpx.Response(200, content=b"mp4")
            )
            await backend.generate(request)
        return [f"{c.request.method} {c.request.url.raw_path.decode()}" for c in route.calls]

    async def test_each_request_takes_its_own_override(self, tmp_path: Path):
        overrides = {
            "submit": "/veo/{{ model }}/submit",
            "poll": "/veo/ops/{{ task_id }}?m={{ model }}",
            "download": "/veo/files/{{ file_id }}",
        }

        assert await self._run(overrides, tmp_path) == [
            "POST /google/v1/veo/veo-x/submit",
            f"GET /google/v1/veo/ops/{OPERATION}?m=veo-x",
            "GET /google/v1/veo/files/abc123?alt=media",
        ]

    async def test_overriding_poll_leaves_submit_and_download_alone(self, tmp_path: Path):
        assert await self._run({"poll": "/ops/{{ task_id }}"}, tmp_path) == [
            "POST /google/v1beta/models/veo-x:predictLongRunning",
            f"GET /google/v1/ops/{OPERATION}",
            "GET /google/v1beta/files/abc123:download?alt=media",
        ]
