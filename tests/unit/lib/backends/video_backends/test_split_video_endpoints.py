"""同一调用端点更换型号名，请求与计量保持一致。"""

import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.video_backends.agnes import Agnes25VideoBackend
from tests.http_capture import capture_http, request_json


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["agnes-video-v2.0", "opaque-model"])
async def test_agnes_25_endpoint_sends_25_contract_for_any_model(tmp_path, model):
    backend = Agnes25VideoBackend(api_key="key", model=model)
    request = VideoGenerationRequest(prompt="p", duration_seconds=5, output_path=tmp_path / "out.mp4")
    with capture_http() as router:
        submit = router.post("https://apihub.agnes-ai.com/v1/videos").respond(200, json={"video_id": "job"})
        router.get("https://apihub.agnes-ai.com/agnesapi").respond(
            200, json={"status": "completed", "url": "https://cdn.test/out.mp4", "seconds": "5"}
        )
        router.get("https://cdn.test/out.mp4").respond(200, content=b"video")
        result = await backend.generate(request)
    body = request_json(submit.calls[0].request)
    assert body == {
        "model": model,
        "prompt": "p",
        "seconds": "5",
        "size": "720P",
        "aspect_ratio": "9:16",
        "mode": "text",
    }
    assert result.generate_audio is True
    assert backend.metered_usage(model, request, result).dimensions["audio"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["wan3.0-video", "opaque-model"])
@pytest.mark.parametrize(
    ("endpoint", "audio", "voice"),
    [
        ("dashscope-video", None, False),
        ("dashscope-always-on-video", None, False),
        ("dashscope-reference-video", None, False),
        ("dashscope-reference-voice-video", None, True),
        ("dashscope-wan3-video", False, False),
    ],
)
async def test_dashscope_endpoint_controls_request_and_metering(tmp_path, endpoint, audio, voice, model):
    from lib.backends.video_backends.dashscope import DASHSCOPE_VIDEO_BACKENDS

    image = tmp_path / "ref.png"
    image.write_bytes(b"image")
    sound = tmp_path / "voice.wav"
    sound.write_bytes(b"sound")
    backend = DASHSCOPE_VIDEO_BACKENDS[endpoint](api_key="key", model=model, base_url="https://dash.test")
    request = VideoGenerationRequest(
        prompt="p",
        output_path=tmp_path / "out.mp4",
        duration_seconds=5,
        generate_audio=audio is not False,
        reference_images=[image],
        reference_audio_files=[sound],
    )
    with capture_http() as router:
        submit = router.post("https://dash.test/api/v1/services/aigc/video-generation/video-synthesis").respond(
            200, json={"output": {"task_id": "job"}}
        )
        router.get("https://dash.test/api/v1/tasks/job").respond(
            200,
            json={
                "output": {"task_status": "SUCCEEDED", "video_url": "https://cdn.test/out.mp4"},
                "usage": {"duration": 5},
            },
        )
        router.get("https://cdn.test/out.mp4").respond(200, content=b"video")
        result = await backend.generate(request)
    body = request_json(submit.calls[0].request)
    assert body["model"] == model
    assert body["parameters"].get("audio") is audio
    media = body["input"]["media"]
    assert [item["type"] for item in media] == (
        ["reference_image"] if voice else ["reference_image", "reference_audio"]
    )
    assert ("reference_voice" in media[0]) is voice
    usage = backend.metered_usage(model, request, result)
    assert usage.dimensions["audio"] is (audio is not False)
    assert usage.quantities["output_seconds"] == 5


@pytest.mark.parametrize("model", ["veo-3.1-generate-001", "opaque-model"])
def test_veo_audio_is_declared_by_credential_protocol(model):
    from arcreel_market_core.video_backend_contract import VideoAudioMode
    from lib.backends.backend_assembly.specs import declared_builtin_video_capabilities
    from lib.backends.video_backends.gemini import GeminiVideoBackend, VertexVideoBackend

    assert GeminiVideoBackend.video_capabilities_for_model(model).audio_track is VideoAudioMode.ALWAYS_ON
    assert VertexVideoBackend.video_capabilities_for_model(model).audio_track is VideoAudioMode.CONTROLLABLE
    assert declared_builtin_video_capabilities("gemini-vertex", model).audio_track is VideoAudioMode.CONTROLLABLE
