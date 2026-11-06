"""音色接口经真实配置与端点装配，按模型和请求语言列音色，不发供应商请求。"""

import httpx
import pytest
from fastapi import FastAPI

from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from server.routers import system_config
from server.services.tasks.generation_context import reset_backend_cache_for_tests
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import bind_session_factory
from tests.http_capture import capture_http


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
    db_factory, monkeypatch, endpoint, model, supports_speed, first_voice
):
    bind_session_factory(monkeypatch, db_factory)
    reset_backend_cache_for_tests()
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

    app = FastAPI()
    override_auth(app)
    app.include_router(system_config.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    try:
        with capture_http():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/v1/system/tts-model-capabilities",
                    params={"backend": backend},
                    headers={"Accept-Language": "en"},
                )
        assert response.status_code == 200
        body = response.json()
        assert body["supports_speed"] is supports_speed
        assert body["voices"][0] == first_voice
    finally:
        reset_backend_cache_for_tests()
