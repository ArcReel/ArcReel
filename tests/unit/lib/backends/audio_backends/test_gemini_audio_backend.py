"""GeminiAudioBackend：respx 拦截 generateContent，验证 PCM→WAV 封装与请求体。"""

from __future__ import annotations

import base64
import wave

import httpx
import pytest

from lib.backends.audio_backends import AudioCapability, AudioSynthesisRequest
from lib.backends.audio_backends.gemini import GeminiAudioBackend
from tests.http_capture import capture_http, only_request, request_json

_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent"


def _audio_response(data: bytes, mime_type: str) -> httpx.Response:
    part = {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(data).decode()}}
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [part]}}]})


def _field(obj: dict, snake: str):
    head, *rest = snake.split("_")
    return obj.get(snake, obj.get(head + "".join(w.title() for w in rest)))


def _request(tmp_path, **kwargs) -> AudioSynthesisRequest:
    return AudioSynthesisRequest(text="你好，世界", output_path=tmp_path / "out.wav", voice="Kore", **kwargs)


@pytest.fixture
async def backend():
    from google import genai

    backend = GeminiAudioBackend(api_key="sk-test", model="gemini-3.8-flash-tts", provider_name="custom-7")
    # SDK 装有 aiohttp 时默认不走 httpx，注入 httpx 客户端让 respx 截获
    async with httpx.AsyncClient() as http_client:
        backend._client = genai.Client(api_key="sk-test", http_options={"httpx_async_client": http_client})
        yield backend


async def test_pcm_is_wrapped_as_wav_matching_rate_and_channels(backend, tmp_path):
    pcm = bytes(range(256)) * 8
    with capture_http() as router:
        route = router.post(_URL).mock(return_value=_audio_response(pcm, "audio/L16;codec=pcm;rate=16000;channels=2"))
        result = await backend.synthesize(_request(tmp_path, speed=1.5))

    body = request_json(only_request(route))
    assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
    # google-genai 2.25 把 speechConfig 内层按 snake_case 序列化；proto3 JSON 两种字段名服务端都接受
    voice_config = _field(body["generationConfig"]["speechConfig"], "voice_config")
    assert _field(_field(voice_config, "prebuilt_voice_config"), "voice_name") == "Kore"
    assert body["contents"][0]["parts"][0]["text"] == "你好，世界"
    with wave.open(str(result.output_path), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 2, 2)
        assert wav.readframes(wav.getnframes()) == pcm
    assert (result.provider, result.model, result.characters) == ("custom-7", "gemini-3.8-flash-tts", 5)


async def test_pcm_without_params_defaults_to_24k_mono(backend, tmp_path):
    with capture_http() as router:
        router.post(_URL).mock(return_value=_audio_response(b"\x00\x01" * 100, "audio/L16;codec=pcm"))
        result = await backend.synthesize(_request(tmp_path))
    with wave.open(str(result.output_path), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels()) == (24000, 1)


async def test_wav_response_is_written_as_is(backend, tmp_path):
    wav_bytes = b"RIFF" + b"\x00" * 60
    with capture_http() as router:
        router.post(_URL).mock(return_value=_audio_response(wav_bytes, "audio/wav"))
        result = await backend.synthesize(_request(tmp_path))
    assert result.output_path.read_bytes() == wav_bytes


async def test_compressed_audio_is_rejected_instead_of_wrapped_as_pcm(backend, tmp_path):
    with capture_http() as router:
        router.post(_URL).mock(return_value=_audio_response(b"ID3" + b"\x00" * 60, "audio/mpeg"))
        with pytest.raises(RuntimeError, match="audio/mpeg"):
            await backend.synthesize(_request(tmp_path))
    assert not (tmp_path / "out.wav").exists()


async def test_no_audio_fails_without_writing(backend, tmp_path):
    empty = httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": "no"}]}}]})
    with capture_http() as router:
        route = router.post(_URL).mock(return_value=empty)
        with pytest.raises(RuntimeError, match="未返回音频"):
            await backend.synthesize(_request(tmp_path))
    assert route.call_count == 1
    assert not (tmp_path / "out.wav").exists()


async def test_declares_voices_without_speed(backend):
    assert backend.capabilities == {AudioCapability.TEXT_TO_SPEECH}
    voices = backend.list_voices()
    assert len(voices) == 30
    assert "Kore" in {v.id for v in voices}
