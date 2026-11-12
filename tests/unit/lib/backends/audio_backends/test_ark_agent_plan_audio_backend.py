"""方舟 Agent Plan 语音合成的装配与声明；线路协议见 tests/integration/lib/backends/audio_backends/test_ark_agent_plan_tts_protocol.py。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from arcreel_catalog_core import endpoint_version_floor
from lib.backends.audio_backends import AudioCapability, AudioSynthesisRequest
from lib.backends.audio_backends.ark_agent_plan import ArkAgentPlanAudioBackend
from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import PROVIDER_ONLY_ENDPOINTS, builtin_model_spec, infer_builtin_endpoint
from lib.config.registry import PROVIDER_REGISTRY
from lib.i18n import SUPPORTED_LOCALES
from lib.i18n import _ as translate
from tests.http_capture import capture_http, only_request, request_json


def _backend(credentials: dict[str, str]):
    spec = builtin_model_spec("ark-agent-plan", "audio", "seed-tts-2.0")
    return spec.build_backend(
        LoadedConfig(credentials=credentials, provider_meta=PROVIDER_REGISTRY["ark-agent-plan"], rate_limiter=None),
        "seed-tts-2.0",
    )


def test_reuses_the_plan_api_key_and_ignores_the_plan_base_url():
    # ark-agent-plan 的默认地址是 /api/plan/v3，语音合成不经它，构造时不接收 base_url
    backend = _backend({"api_key": " plan-key ", "base_url": "https://ark.example/api/plan/v3"})
    assert isinstance(backend, ArkAgentPlanAudioBackend)
    assert (backend.name, backend.model) == ("ark-agent-plan", "seed-tts-2.0")


async def test_assembled_backend_posts_to_the_plan_tts_url_with_the_plan_key(tmp_path: Path):
    body = (
        json.dumps({"code": 0, "message": "", "data": base64.b64encode(b"\x00\x00").decode()})
        + "\n"
        + json.dumps({"code": 20000000, "message": "ok", "data": None, "usage": {"text_words": 2}})
    )
    with capture_http() as router:
        route = router.post("https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional").mock(
            return_value=httpx.Response(200, text=body, headers={"Content-Type": "application/json"})
        )
        result = await _backend({"api_key": "plan-key"}).synthesize(
            AudioSynthesisRequest(text="你好", output_path=tmp_path / "o.wav", voice="zh_female_vv_uranus_bigtts")
        )
    submitted = only_request(route)
    assert (submitted.headers["X-Api-Key"], submitted.headers["X-Api-Resource-Id"]) == ("plan-key", "seed-tts-2.0")
    assert request_json(submitted)["req_params"]["speaker"] == "zh_female_vv_uranus_bigtts"
    assert result.billed_characters == 2


def test_missing_api_key_fails_at_construction():
    with pytest.raises(ValueError, match="Agent Plan API Key"):
        _backend({})


def test_declares_speed_and_a_voice_list_with_translated_labels():
    backend = _backend({"api_key": "plan-key"})
    assert backend.capabilities == {AudioCapability.TEXT_TO_SPEECH, AudioCapability.SPEECH_SPEED}
    voices = backend.list_voices()
    # 新建项目预填首项：取 Agent Plan 文档示例里的音色
    assert voices[0].id == "zh_female_vv_uranus_bigtts"
    assert all(voice.id.endswith("_uranus_bigtts") for voice in voices)
    for locale in SUPPORTED_LOCALES:
        labels = [translate(voice.label, locale=locale) for voice in voices]
        assert all(label != voice.label for label, voice in zip(labels, voices, strict=True)), locale


def test_endpoint_is_builtin_only_and_user_added_models_default_to_it():
    assert PROVIDER_ONLY_ENDPOINTS["ark-agent-plan-tts"] == "audio"
    assert infer_builtin_endpoint("ark-agent-plan", "audio", "any-future-name") == "ark-agent-plan-tts"


def test_models_on_the_endpoint_require_the_first_version_that_ships_it():
    assert endpoint_version_floor("ark-agent-plan", "audio", "ark-agent-plan-tts") == "0.36.1"
