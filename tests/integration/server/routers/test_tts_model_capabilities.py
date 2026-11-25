"""音色接口从静态声明读取自定义端点的能力和本地化音色。"""

import httpx
import pytest
from fastapi import FastAPI

from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from server.routers import system_config
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.http_capture import capture_http


@pytest.fixture
async def client(db_factory):
    app = FastAPI()

    async def get_session():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[system_config.get_async_session] = get_session
    override_auth(app)
    app.include_router(system_config.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        yield api


@pytest.mark.parametrize(
    ("endpoint", "model", "supports_speed", "first_voice"),
    [
        (
            "dashscope-tts",
            "qwen3-tts-flash",
            False,
            {"id": "Cherry", "label": "Cherry — Warm, upbeat natural young female voice"},
        ),
        (
            "dashscope-speech-synthesizer",
            "qwen-audio-3.1-tts-plus",
            True,
            {"id": "longanhuan_v3.1", "label": "Long Anhuan — Female voice"},
        ),
    ],
)
async def test_voices_follow_model_endpoint_and_request_locale(
    db_factory, client, endpoint, model, supports_speed, first_voice
):
    async with db_factory() as session:
        provider = CustomProvider(
            display_name="TTS", discovery_format="openai", base_url="https://tts.test", api_key="test"
        )
        session.add(provider)
        await session.flush()
        session.add(
            CustomProviderModel(
                provider_id=provider.id, model_id=model, display_name=model, endpoint=endpoint, is_enabled=True
            )
        )
        await session.commit()
        backend = f"{provider.provider_id}/{model}"

    with capture_http():
        response = await client.get(
            "/api/v1/system/tts-model-capabilities",
            params={"backend": backend},
            headers={"Accept-Language": "en"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["supports_speed"] is supports_speed
    assert body["voices"][0] == first_voice


@pytest.mark.parametrize(
    ("endpoint", "known_model", "unknown_model", "known_voice"),
    [
        ("dashscope-speech-synthesizer", "qwen-audio-3.0-tts-plus", "qwen-audio-3.1-tts-next", "longanlingxin"),
        ("minimax-tts", "speech-2.8-hd", "speech-2.9-hd", "male-qn-qingse"),
    ],
)
async def test_same_endpoint_unknown_model_lists_no_voices(
    db_factory, client, endpoint, known_model, unknown_model, known_voice
):
    async with db_factory() as session:
        provider = CustomProvider(
            display_name="TTS", discovery_format="openai", base_url="https://tts.test", api_key="test"
        )
        session.add(provider)
        await session.flush()
        for model in (known_model, unknown_model):
            session.add(
                CustomProviderModel(
                    provider_id=provider.id, model_id=model, display_name=model, endpoint=endpoint, is_enabled=True
                )
            )
        await session.commit()
        provider_id = provider.provider_id

    with capture_http():
        known, unknown = [
            await client.get("/api/v1/system/tts-model-capabilities", params={"backend": f"{provider_id}/{model}"})
            for model in (known_model, unknown_model)
        ]
    assert known.status_code == unknown.status_code == 200
    assert known_voice in [v["id"] for v in known.json()["voices"]]
    assert unknown.json() == {"supports_speed": known.json()["supports_speed"], "voices": []}


async def test_discovered_model_requires_voice_before_pinning_the_group(db_factory, client, monkeypatch):
    from lib.config.recommended_models import FROZEN_SETTING_KEY
    from lib.config.service import ConfigService

    monkeypatch.setattr(system_config, "async_session_factory", db_factory)
    async with db_factory() as session:
        provider = CustomProvider(
            display_name="Relay", discovery_format="openai", base_url="https://relay.test", api_key=""
        )
        session.add(provider)
        await session.flush()
        session.add(
            CustomProviderModel(
                provider_id=provider.id,
                model_id="reader",
                display_name="Reader",
                endpoint="openai-tts",
                is_default=True,
                is_enabled=True,
            )
        )
        pair = f"{provider.provider_id}/reader"
        settings = ConfigService(session)
        await settings.set_setting(FROZEN_SETTING_KEY, "[]")
        await settings.set_setting("narration_voice", "orphan")
        await settings.set_setting("narration_speed", "1.2")
        await session.commit()
    auto = await client.get("/api/v1/system/config/auto-backends")
    assert auto.json()["audio"] == pair
    initial = (await client.get("/api/v1/system/config")).json()["settings"]
    assert (initial["default_audio_backend"], initial["narration_voice"], initial["narration_speed"]) == ("", "", None)
    saved = (
        await client.patch("/api/v1/system/config", json={"default_audio_backend": pair, "narration_voice": "reader-1"})
    ).json()["settings"]
    assert (saved["default_audio_backend"], saved["narration_voice"], saved["narration_speed"]) == (
        pair,
        "reader-1",
        None,
    )
