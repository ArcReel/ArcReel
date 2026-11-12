"""供应商并发字段随目录 lane 显隐，隐藏时保留用户配置。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from arcreel_catalog_core import AudioFacts, CatalogEntry, ImageFacts, VideoFacts
from lib.config.registry import PROVIDER_REGISTRY
from lib.db import get_async_session
from lib.generation.generation_worker import CapacityTable, GenerationWorker
from server.routers import providers
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.catalog_support import catalog_of, install_catalog
from tests.fakes import bind_safe_session_factory, refuse_resume_execution


def _grok_catalog(*, audio: bool):
    entries = (
        CatalogEntry(maker="xai", name="image", media_type="image", display_name="Image", facts=ImageFacts()),
        CatalogEntry(maker="xai", name="video", media_type="video", display_name="Video", facts=VideoFacts()),
    )
    if audio:
        entries += (
            CatalogEntry(maker="xai", name="grok-tts", media_type="audio", display_name="TTS", facts=AudioFacts()),
        )
    return catalog_of(entries, {"grok": tuple(entry.key.ref for entry in entries)})


@pytest.fixture
def config_client(db_factory):
    app = FastAPI()

    async def session():
        async with db_factory() as value:
            yield value

    app.dependency_overrides[get_async_session] = session
    override_auth(app)
    app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("audio", [False, True])
def test_grok_concurrency_fields_follow_catalog(config_client, monkeypatch, audio):
    install_catalog(monkeypatch, _grok_catalog(audio=audio))
    response = config_client.get("/api/v1/providers/grok/config")
    assert response.status_code == 200
    expected = {"image_max_workers", "video_max_workers"}
    if audio:
        expected.add("audio_max_workers")
    assert {field["key"] for field in response.json()["fields"]} == expected


def test_other_provider_fields_are_unchanged(config_client):
    for provider, meta in PROVIDER_REGISTRY.items():
        if provider == "grok":
            continue
        response = config_client.get(f"/api/v1/providers/{provider}/config")
        assert response.status_code == 200
        expected = set(meta.required_keys + meta.optional_keys) - {
            "api_key",
            "credentials_path",
            "base_url",
            "access_key",
            "secret_key",
        }
        if provider == "openai":
            expected.remove("video_max_workers")
        assert {field["key"] for field in response.json()["fields"]} == expected


def test_save_reloads_audio_capacity_and_hidden_value_survives(config_client, db_factory, monkeypatch):
    install_catalog(monkeypatch, _grok_catalog(audio=True))
    bind_safe_session_factory(monkeypatch, db_factory)
    capacity = CapacityTable.from_env()

    async def unexpected_execution(task, *, claimed_provider_id):
        raise AssertionError("configuration updates must not generate media")

    worker = GenerationWorker(capacity=capacity, executor=unexpected_execution, resume_executor=refuse_resume_execution)
    config_client.app.state.generation_worker = worker
    response = config_client.patch("/api/v1/providers/grok/config", json={"audio_max_workers": "2"})
    assert response.status_code == 204
    assert capacity.get("grok", "audio") == 2

    install_catalog(monkeypatch, _grok_catalog(audio=False))
    fields = config_client.get("/api/v1/providers/grok/config").json()["fields"]
    assert "audio_max_workers" not in {field["key"] for field in fields}
    # 保存仍可见的字段不能清除已隐藏的音频值。
    assert config_client.patch("/api/v1/providers/grok/config", json={"image_max_workers": "3"}).status_code == 204
    assert capacity.get("grok", "audio") == 0

    install_catalog(monkeypatch, _grok_catalog(audio=True))
    fields = config_client.get("/api/v1/providers/grok/config").json()["fields"]
    audio = next(field for field in fields if field["key"] == "audio_max_workers")
    assert (audio["value"], audio["is_set"]) == ("2", True)
    assert config_client.patch("/api/v1/providers/grok/config", json={"video_max_workers": "4"}).status_code == 204
    assert capacity.get("grok", "audio") == 2
