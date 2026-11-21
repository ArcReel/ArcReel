"""MiniMaxAudioBackend：同步 ``t2a_v2`` 一次合成，respx 在 transport 层捕获真实请求。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lib.backends.audio_backends import AudioCapability, AudioSynthesisRequest
from lib.backends.audio_backends.minimax import MiniMaxAudioBackend
from tests.http_capture import capture_http, only_request, request_json

_T2A_URL = "https://api.minimaxi.com/v1/t2a_v2"


def _ok(audio: bytes) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {"audio": audio.hex(), "status": 2},
            "extra_info": {"audio_format": "wav", "usage_characters": 8},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        },
    )


async def test_synthesize_decodes_hex_audio_to_file(tmp_path: Path):
    with capture_http() as router:
        route = router.post(_T2A_URL).mock(return_value=_ok(b"RIFFwavbytes"))
        # 用户填 host 不带 /v1 也应补齐
        b = MiniMaxAudioBackend(api_key="sk", base_url="https://api.minimaxi.com", model="speech-2.8-hd")
        out = tmp_path / "o.wav"
        result = await b.synthesize(
            AudioSynthesisRequest(text="你好世界", output_path=out, voice="female-shaonv", speed=1.2)
        )

    submitted = only_request(route)
    assert submitted.headers["Authorization"] == "Bearer sk"
    body = request_json(submitted)
    assert body["model"] == "speech-2.8-hd"
    assert body["text"] == "你好世界"
    assert body["stream"] is False
    assert body["output_format"] == "hex"
    assert body["voice_setting"] == {"voice_id": "female-shaonv", "speed": 1.2}
    assert body["audio_setting"]["format"] == "wav"
    assert out.read_bytes() == b"RIFFwavbytes"
    assert result.provider == "minimax"
    assert result.text == "你好世界"


async def test_base_resp_error_raises_without_file(tmp_path: Path):
    with capture_http() as router:
        router.post(_T2A_URL).mock(
            return_value=httpx.Response(
                200, json={"data": None, "base_resp": {"status_code": 2013, "status_msg": "invalid voice"}}
            )
        )
        b = MiniMaxAudioBackend(api_key="sk")
        out = tmp_path / "e.wav"
        with pytest.raises(RuntimeError, match="2013"):
            await b.synthesize(AudioSynthesisRequest(text="x", output_path=out, voice="nope"))
    assert not out.exists()


def test_declares_speed_and_voice_catalog():
    b = MiniMaxAudioBackend(api_key="sk", provider_name="my-relay")
    assert b.name == "my-relay"
    assert AudioCapability.SPEECH_SPEED in b.capabilities
    ids = [v.id for v in b.list_voices()]
    assert "male-qn-qingse" in ids
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    ("model", "listed"),
    [
        # t2a_v2 的 voice_id 引用同一份系统音色列表，没有按型号区分；model 取值是官方枚举里的型号
        ("speech-2.8-turbo", True),
        ("speech-02-hd", True),
        ("speech-01-turbo", True),
        # 同端点但不在官方枚举里：认不出是哪个模型，不借用系统音色
        ("speech-2.9-hd", False),
        ("speech-1.5", False),
    ],
)
def test_system_voices_are_listed_only_for_official_t2a_models(model: str, listed: bool):
    ids = [v.id for v in MiniMaxAudioBackend(api_key="sk", model=model, provider_name="my-relay").list_voices()]
    assert ("female-shaonv" in ids) is listed
    assert bool(ids) is listed


async def test_custom_endpoint_routes_to_relay_and_bills_provider(tmp_path: Path):
    from types import SimpleNamespace

    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

    provider = SimpleNamespace(provider_id="custom-7", api_key="sk", base_url="https://relay.example.com/v1")
    backend = ENDPOINT_REGISTRY["minimax-tts"].build_backend(provider, "speech-2.8-turbo")
    with capture_http() as router:
        route = router.post("https://relay.example.com/v1/t2a_v2").mock(return_value=_ok(b"ID3mp3"))
        result = await backend.synthesize(
            AudioSynthesisRequest(text="hi", output_path=tmp_path / "o.mp3", voice="female-yujie")
        )

    assert request_json(only_request(route))["audio_setting"]["format"] == "mp3"
    assert result.provider == "custom-7"
    assert backend.name == "custom-7"
