"""内置 grok 供应商的语音合成：经实际装配路径发出的 ``POST /v1/tts`` 请求，respx 在 transport 层捕获。"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import httpx
import pytest

from arcreel_catalog_core import endpoint_version_floor
from lib.backends.audio_backends import AudioCapability, AudioSynthesisRequest
from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import PROVIDER_ONLY_ENDPOINTS, builtin_model_spec, infer_builtin_endpoint
from lib.backends.http_status_errors import AmbiguousSubmitError
from lib.config.registry import PROVIDER_REGISTRY
from lib.generation.task_failure import render_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.i18n import _ as translate
from lib.infra.api_errors import UnprocessableError
from tests.factories import make_display_names
from tests.http_capture import capture_http, only_request, request_json

_TTS_URL = "https://api.x.ai/v1/tts"
_MODEL = "grok-tts"


def _backend(model: str | None = _MODEL):
    spec = builtin_model_spec("grok", "audio", model)
    return spec.build_backend(
        LoadedConfig(credentials={"api_key": "xai-key"}, provider_meta=PROVIDER_REGISTRY["grok"], rate_limiter=None),
        model,
    )


def _audio(body: bytes = b"RIFFwave", content_type: str = "audio/wav") -> httpx.Response:
    return httpx.Response(200, content=body, headers={"Content-Type": content_type})


def _request(tmp_path: Path, *, name: str = "o.wav", text: str = "你好世界", **kwargs) -> AudioSynthesisRequest:
    return AudioSynthesisRequest(text=text, output_path=tmp_path / name, voice="eve", **kwargs)


async def test_assembled_backend_sends_the_documented_request_without_a_model(tmp_path: Path):
    with capture_http() as router:
        route = router.post(_TTS_URL).mock(return_value=_audio())
        result = await _backend().synthesize(_request(tmp_path, speed=1.2))

    submitted = only_request(route)
    assert submitted.method == "POST"
    assert str(submitted.url) == _TTS_URL
    assert submitted.headers["Authorization"] == "Bearer xai-key"
    assert request_json(submitted) == {
        "text": "你好世界",
        "voice_id": "eve",
        "language": "zh",
        "output_format": {"codec": "wav"},
        "speed": 1.2,
    }
    assert (tmp_path / "o.wav").read_bytes() == b"RIFFwave"
    assert (result.provider, result.model, result.text) == ("grok", _MODEL, "你好世界")


async def test_missing_model_falls_back_to_the_local_identifier(tmp_path: Path):
    with capture_http() as router:
        router.post(_TTS_URL).mock(return_value=_audio())
        result = await _backend(None).synthesize(_request(tmp_path))
    assert result.model == _MODEL


@pytest.mark.parametrize(("name", "codec"), [("o.wav", "wav"), ("o.mp3", "mp3"), ("o.pcm", "pcm"), ("o.ogg", "wav")])
async def test_output_codec_follows_the_artifact_extension(tmp_path: Path, name: str, codec: str):
    with capture_http() as router:
        route = router.post(_TTS_URL).mock(return_value=_audio(b"bytes", "audio/mpeg"))
        await _backend().synthesize(_request(tmp_path, name=name))
    assert request_json(only_request(route))["output_format"] == {"codec": codec}


@pytest.mark.parametrize(
    ("language_type", "text", "expected"),
    [
        ("Chinese", "你好世界", "zh"),
        ("Chinese", "English narration only", "auto"),
        ("Chinese", "こんにちは世界", "auto"),
        ("Chinese", "안녕하세요 世界", "auto"),
        ("English", "hello", "en"),
        ("japanese", "こんにちは", "ja"),
        ("Klingon", "nuqneH", "auto"),
    ],
)
async def test_internal_language_is_converted_never_sent_verbatim(
    tmp_path: Path, language_type: str, text: str, expected: str
):
    with capture_http() as router:
        route = router.post(_TTS_URL).mock(return_value=_audio())
        await _backend().synthesize(_request(tmp_path, text=text, language_type=language_type))
    assert request_json(only_request(route))["language"] == expected


@pytest.mark.parametrize("speed", [0.7, 1.0, 1.5])
async def test_speed_inside_the_official_range_is_sent_unchanged(tmp_path: Path, speed: float):
    with capture_http() as router:
        route = router.post(_TTS_URL).mock(return_value=_audio())
        await _backend().synthesize(_request(tmp_path, speed=speed))
    assert request_json(only_request(route))["speed"] == speed


async def test_unset_speed_is_omitted(tmp_path: Path):
    with capture_http() as router:
        route = router.post(_TTS_URL).mock(return_value=_audio())
        await _backend().synthesize(_request(tmp_path))
    assert "speed" not in request_json(only_request(route))


@pytest.mark.parametrize("speed", [0.69, 1.51, 2.0])
async def test_out_of_range_speed_fails_before_any_billed_request(tmp_path: Path, speed: float):
    with capture_http() as router:
        route = router.post(_TTS_URL).mock(return_value=_audio())
        with pytest.raises(UnprocessableError) as caught:
            await _backend().synthesize(_request(tmp_path, speed=speed))
    assert route.call_count == 0
    assert not (tmp_path / "o.wav").exists()

    # 任务失败原因落结构化编码，读侧按语言渲染，并写明可用范围与当前值
    stored = encode_task_failure_message(caught.value)
    for locale in ("zh", "en"):
        rendered = render_failure(stored, partial(translate, locale=locale), make_display_names(locale))
        assert rendered is not None
        assert all(value in rendered for value in ("0.7", "1.5", str(speed)))


def test_declares_speed_and_the_official_voice_list():
    backend = _backend()
    assert backend.capabilities == {AudioCapability.TEXT_TO_SPEECH, AudioCapability.SPEECH_SPEED}
    voices = {voice.id: voice.label for voice in backend.list_voices()}
    assert len(voices) == 26
    assert voices["eve"] == "Eve"
    assert {"ara", "rex", "sal", "leo", "carina", "naksh"} <= set(voices)


async def test_ambiguous_transport_failure_is_not_resubmitted(tmp_path: Path):
    with capture_http() as router:
        route = router.post(_TTS_URL).mock(side_effect=httpx.ReadTimeout("boom"))
        with pytest.raises(AmbiguousSubmitError):
            await _backend().synthesize(_request(tmp_path))
    assert route.call_count == 1


async def test_explicit_server_error_is_retried(tmp_path: Path, poll_clock):
    with capture_http() as router:
        route = router.post(_TTS_URL).mock(side_effect=[httpx.Response(503), _audio(b"RIFFok")])
        await _backend().synthesize(_request(tmp_path))
    assert route.call_count == 2
    assert (tmp_path / "o.wav").read_bytes() == b"RIFFok"


async def test_write_failure_does_not_synthesize_again(tmp_path: Path):
    request = AudioSynthesisRequest(text="x", output_path=tmp_path / "missing" / "o.wav", voice="eve")
    with capture_http() as router:
        route = router.post(_TTS_URL).mock(return_value=_audio())
        with pytest.raises(FileNotFoundError):
            await _backend().synthesize(request)
    assert route.call_count == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"", headers={"Content-Type": "audio/wav"}),
        httpx.Response(200, json={"error": "nope"}),
        httpx.Response(200, text="<html>gateway</html>", headers={"Content-Type": "text/html"}),
    ],
    ids=["empty", "json", "html"],
)
async def test_non_audio_success_body_never_becomes_an_artifact(tmp_path: Path, response: httpx.Response):
    with capture_http() as router:
        route = router.post(_TTS_URL).mock(return_value=response)
        with pytest.raises(RuntimeError):
            await _backend().synthesize(_request(tmp_path))
    assert route.call_count == 1
    assert not (tmp_path / "o.wav").exists()


def test_endpoint_is_builtin_only_and_user_added_models_default_to_it():
    assert PROVIDER_ONLY_ENDPOINTS["grok-tts"] == "audio"
    assert infer_builtin_endpoint("grok", "audio", "any-future-name") == "grok-tts"


def test_models_on_the_endpoint_require_the_first_version_that_ships_it():
    assert endpoint_version_floor("grok", "audio", "grok-tts") == "0.35.1"
