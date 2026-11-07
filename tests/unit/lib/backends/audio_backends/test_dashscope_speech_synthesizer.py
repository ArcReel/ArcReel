"""DashScopeSpeechSynthesizerBackend: synchronous Bailian SpeechSynthesizer, intercepted at transport by respx.

Fixtures use minimal official nonstreaming examples: https://help.aliyun.com/zh/model-studio/qwen-audio-tts-http-api ;
3.1 token fields follow that page’s ``usage`` section.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
import respx

from lib.backends.audio_backends import AudioCapability, AudioSynthesisRequest, create_backend
from lib.backends.audio_backends.dashscope_speech_synthesizer import (
    DashScopeSpeechSynthesizerBackend,
    voices_for_model,
)
from lib.backends.providers import PROVIDER_DASHSCOPE
from lib.i18n.en.assets import MESSAGES as EN_ASSETS
from lib.i18n.ru.assets import MESSAGES as RU_ASSETS
from lib.i18n.vi.assets import MESSAGES as VI_ASSETS
from lib.i18n.zh.assets import MESSAGES as ZH_ASSETS
from tests.http_capture import capture_http, only_request, request_json

_HOST = "https://dashscope.aliyuncs.com"
_SYNTH_URL = f"{_HOST}/api/v1/services/audio/tts/SpeechSynthesizer"
_AUDIO_URL = "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/out.wav?Expires=1&Signature=s"


def _official_response(usage: dict) -> dict:
    """Official nonstreaming responses put audio at output.audio.url and leave output.audio.data empty."""
    return {
        "request_id": "ee88b03d-0457-9286-8c67-000000000000",
        "output": {
            "finish_reason": "stop",
            "audio": {
                "data": "",
                "url": _AUDIO_URL,
                "id": "audio_ee88b03d-0457-9286-8c67-000000000000",
                "expires_at": 1772697707,
            },
        },
        "usage": usage,
    }


class _Routes(NamedTuple):
    synthesize: respx.Route
    download: respx.Route


@contextmanager
def _routes(
    *, synth: httpx.Response | None = None, synth_url: str = _SYNTH_URL, download: bytes = b"RIFFwav"
) -> Generator[_Routes]:
    with capture_http() as router:
        synth_route = router.post(synth_url)
        synth_route.mock(return_value=synth or httpx.Response(200, json=_official_response({"characters": 15})))
        download_route = router.get(_AUDIO_URL)
        download_route.mock(return_value=httpx.Response(200, content=download))
        yield _Routes(synthesize=synth_route, download=download_route)


def _request(tmp_path: Path, *, name: str = "o.wav", speed: float | None = None) -> AudioSynthesisRequest:
    return AudioSynthesisRequest(
        text="我家的后面有一个很大的花园。", output_path=tmp_path / name, voice="longanhuan_v3.1", speed=speed
    )


class TestMetadata:
    def test_registered_under_its_endpoint_name(self):
        backend = create_backend("dashscope-speech-synthesizer", api_key="sk", model="qwen-audio-3.0-tts-flash")
        assert isinstance(backend, DashScopeSpeechSynthesizerBackend)
        assert backend.name == PROVIDER_DASHSCOPE
        assert backend.model == "qwen-audio-3.0-tts-flash"

    def test_default_model_and_speed_capability(self):
        backend = DashScopeSpeechSynthesizerBackend(api_key="sk")
        assert backend.model == "qwen-audio-3.1-tts-flash"
        assert backend.capabilities == {AudioCapability.TEXT_TO_SPEECH, AudioCapability.SPEECH_SPEED}


class TestRequest:
    async def test_posts_speech_synthesizer_body_and_downloads_audio_url(self, tmp_path: Path):
        with _routes(download=b"RIFFbytes") as routes:
            backend = DashScopeSpeechSynthesizerBackend(api_key="sk", model="qwen-audio-3.0-tts-flash", base_url=_HOST)
            result = await backend.synthesize(_request(tmp_path))

        submitted = only_request(routes.synthesize)
        assert submitted.url.path == "/api/v1/services/audio/tts/SpeechSynthesizer"
        assert submitted.headers["Authorization"] == "Bearer sk"
        assert "X-DashScope-Async" not in submitted.headers
        assert "X-DashScope-SSE" not in submitted.headers
        # Formats follow extensions; omit language_type and unspecified rate.
        assert request_json(submitted) == {
            "model": "qwen-audio-3.0-tts-flash",
            "input": {
                "text": "我家的后面有一个很大的花园。",
                "voice": "longanhuan_v3.1",
                "format": "wav",
                "sample_rate": 24000,
            },
        }
        assert str(only_request(routes.download).url) == _AUDIO_URL
        assert (tmp_path / "o.wav").read_bytes() == b"RIFFbytes"
        # 3.0 reports billable characters, not tokens; calculators count original text for character pricing.
        assert result.text == "我家的后面有一个很大的花园。"
        assert result.input_tokens is None
        assert result.audio_output_tokens is None

    async def test_workspace_host_keeps_the_same_path(self, tmp_path: Path):
        host = "https://ws-123.cn-beijing.maas.aliyuncs.com"
        with _routes(synth_url=f"{host}/api/v1/services/audio/tts/SpeechSynthesizer") as routes:
            backend = DashScopeSpeechSynthesizerBackend(api_key="sk", base_url=f"{host}/api/v1")
            await backend.synthesize(_request(tmp_path))
        assert routes.synthesize.call_count == 1

    @pytest.mark.parametrize(("name", "expected"), [("o.mp3", "mp3"), ("o.opus", "opus"), ("o.flac", "wav")])
    async def test_format_follows_output_extension(self, tmp_path: Path, name: str, expected: str):
        with _routes() as routes:
            await DashScopeSpeechSynthesizerBackend(api_key="sk").synthesize(_request(tmp_path, name=name))
        assert request_json(only_request(routes.synthesize))["input"]["format"] == expected

    @pytest.mark.parametrize(("speed", "rate"), [(1.2, 1.2), (0.3, 0.5), (3.0, 2.0)])
    async def test_speed_is_sent_as_rate_within_range(self, tmp_path: Path, speed: float, rate: float):
        with _routes() as routes:
            await DashScopeSpeechSynthesizerBackend(api_key="sk").synthesize(_request(tmp_path, speed=speed))
        body = request_json(only_request(routes.synthesize))["input"]
        assert body["rate"] == rate
        assert "speed" not in body

    async def test_reports_token_usage_of_31_models(self, tmp_path: Path):
        usage = {"characters": 15, "input_tokens": 12, "output_tokens": 340, "total_tokens": 352}
        with _routes(synth=httpx.Response(200, json=_official_response(usage))):
            result = await DashScopeSpeechSynthesizerBackend(api_key="sk").synthesize(_request(tmp_path))
        assert result.input_tokens == 12
        assert result.audio_output_tokens == 340

    @pytest.mark.parametrize("bad", [None, "12", True, -1, 1.5])
    async def test_malformed_token_fields_are_treated_as_unreported(self, tmp_path: Path, bad: object):
        usage = {"characters": 15, "input_tokens": bad, "output_tokens": bad}
        with _routes(synth=httpx.Response(200, json=_official_response(usage))):
            result = await DashScopeSpeechSynthesizerBackend(api_key="sk").synthesize(_request(tmp_path))
        assert result.input_tokens is None
        assert result.audio_output_tokens is None

    async def test_missing_audio_url_fails_without_download(self, tmp_path: Path):
        error = {"code": "InvalidParameter", "message": "[cosyvoice:]Engine error [411]", "request_id": "r"}
        with (
            _routes(synth=httpx.Response(200, json=error)) as routes,
            pytest.raises(RuntimeError, match="InvalidParameter"),
        ):
            await DashScopeSpeechSynthesizerBackend(api_key="sk").synthesize(_request(tmp_path))
        assert routes.download.call_count == 0

    async def test_client_error_is_not_retried(self, tmp_path: Path):
        with (
            _routes(synth=httpx.Response(400, json={"code": "InvalidParameter"})) as routes,
            pytest.raises(httpx.HTTPStatusError),
        ):
            await DashScopeSpeechSynthesizerBackend(api_key="sk").synthesize(_request(tmp_path))
        assert routes.synthesize.call_count == 1
        assert routes.download.call_count == 0


class TestVoices:
    @pytest.mark.parametrize(
        ("model", "included", "excluded"),
        [
            ("qwen-audio-3.1-tts-plus", "xunanchuan_v3.1", "yuxiaoyun_v3.1"),
            ("qwen-audio-3.1-tts-flash", "yuxiaoyun_v3.1", "longanlingxin"),
            ("qwen-audio-3.0-tts-plus", "longanlufeng", "longanhuan_v3.1"),
            ("qwen-audio-3.0-tts-flash", "longanhuan_v3.6", "longanhuan_v3.1"),
            # Relay namespaces/date-snapshot suffixes do not affect generation detection.
            ("aliyun/Qwen-Audio-3.0-TTS-Flash-2026-03-04", "loongjohn", "longanhuan_v3.1"),
        ],
    )
    def test_voices_follow_the_model(self, model: str, included: str, excluded: str):
        ids = [v.id for v in DashScopeSpeechSynthesizerBackend(api_key="sk", model=model).list_voices()]
        assert included in ids
        assert excluded not in ids

    def test_unknown_model_falls_back_to_31_flagship_voices(self):
        ids = [v.id for v in voices_for_model("qwen-audio-3.2-tts-turbo")]
        assert ids == ["longanhuan_v3.1", "longanlingxin_v3.1", "longanfengyue_v3.1", "xunanchuan_v3.1"]

    def test_list_is_a_fresh_copy(self):
        backend = DashScopeSpeechSynthesizerBackend(api_key="sk")
        assert backend.list_voices() is not backend.list_voices()

    @pytest.mark.parametrize(
        "model",
        ["qwen-audio-3.1-tts-plus", "qwen-audio-3.1-tts-flash", "qwen-audio-3.0-tts-plus", "qwen-audio-3.0-tts-flash"],
    )
    def test_every_voice_label_is_translated_in_all_locales(self, model: str):
        voices = voices_for_model(model)
        assert len({v.id for v in voices}) == len(voices)
        for voice in voices:
            for messages in (ZH_ASSETS, EN_ASSETS, VI_ASSETS, RU_ASSETS):
                assert messages.get(voice.label), (model, voice.label)


class TestCustomProviderEndpoint:
    """Custom ``dashscope-speech-synthesizer`` providers build this backend and apply path overrides to SpeechSynthesizer requests."""

    def _build(self, overrides: dict[str, str] | None = None):
        from types import SimpleNamespace
        from typing import Any, cast

        from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

        provider = SimpleNamespace(provider_id="cp", base_url="https://relay.test", api_key="k")
        spec = ENDPOINT_REGISTRY["dashscope-speech-synthesizer"]
        assert spec.media_type == "audio"
        assert [r.name for r in spec.requests] == ["generate"]
        return spec.build_backend(cast("Any", provider), "qwen-audio-3.1-tts-plus", overrides or {})

    def test_wraps_this_backend_with_model_voices(self):
        backend = self._build()
        assert isinstance(backend._delegate, DashScopeSpeechSynthesizerBackend)
        assert [v.id for v in backend.list_voices()] == [v.id for v in voices_for_model("qwen-audio-3.1-tts-plus")]

    async def test_path_override_applies_to_speech_synthesizer_request(self, tmp_path: Path):
        backend = self._build({"generate": "/proxy/tts"})
        with capture_http() as router:
            synth = router.post("https://relay.test/proxy/tts").mock(
                return_value=httpx.Response(200, json=_official_response({"characters": 15}))
            )
            router.get(_AUDIO_URL).mock(return_value=httpx.Response(200, content=b"ok"))
            await backend.synthesize(_request(tmp_path))
        assert synth.call_count == 1
