"""供应商并发字段随目录 lane 显隐，隐藏时保留用户配置。"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from arcreel_catalog_core import AudioFacts, CatalogEntry, ImageFacts, VideoFacts
from lib.catalog import official_sync
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.service import ConfigService
from lib.db import get_async_session
from lib.generation.generation_worker import CapacityTable, GenerationWorker
from server.routers import providers
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.catalog_support import catalog_of, install_catalog, with_dataset_version
from tests.fakes import bind_safe_session_factory, refuse_resume_execution
from tests.integration.server.official_sync_support import NEWER, serve_dataset, sync_as_app_starts_it


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


def _providers_app(session_factory) -> FastAPI:
    app = FastAPI()

    async def session():
        async with session_factory() as value:
            yield value

    app.dependency_overrides[get_async_session] = session
    override_auth(app)
    app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return app


async def _unexpected_execution(task, *, claimed_provider_id):
    raise AssertionError("configuration updates must not generate media")


@pytest.fixture
def config_client(db_factory):
    with TestClient(_providers_app(db_factory)) as client:
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
    worker = GenerationWorker(
        capacity=capacity, executor=_unexpected_execution, resume_executor=refuse_resume_execution
    )
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


async def test_synced_lane_shows_field_and_follows_saved_value_until_sync_removes_it(file_db_factory, monkeypatch):
    """官方同步给 grok 加上音频 lane：设置页出现并发字段，保存后 worker 按该值限流；再次同步移除：字段隐藏、容量为 0，存值留在库里。"""
    install_catalog(monkeypatch, with_dataset_version(_grok_catalog(audio=False), "2026-01-01T00:00:00Z"))
    bind_safe_session_factory(monkeypatch, file_db_factory)
    worker = GenerationWorker(executor=_unexpected_execution, resume_executor=refuse_resume_execution)
    await worker.reload_limits()
    app = _providers_app(file_db_factory)
    # 保存配置的路由与同步回调重载的是同一个 worker
    app.state.generation_worker = worker

    def audio_capacity() -> int:
        return worker._capacity.get("grok", "audio")

    async def sync_to(audio: bool, dataset_version: str, done) -> None:
        upstream = with_dataset_version(_grok_catalog(audio=audio), dataset_version)
        await sync_as_app_starts_it(monkeypatch, file_db_factory, serve_dataset(upstream), done, worker=worker)

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:

            async def field_keys() -> set[str]:
                response = await client.get("/api/v1/providers/grok/config")
                assert response.status_code == 200
                return {field["key"] for field in response.json()["fields"]}

            assert "audio_max_workers" not in await field_keys()
            assert audio_capacity() == 0

            await sync_to(True, NEWER, lambda: audio_capacity() > 0)
            assert "audio_max_workers" in await field_keys()
            response = await client.patch("/api/v1/providers/grok/config", json={"audio_max_workers": "2"})
            assert response.status_code == 204
            assert audio_capacity() == 2

            await sync_to(False, "2099-02-01T00:00:00Z", lambda: audio_capacity() == 0)
            assert "audio_max_workers" not in await field_keys()
            async with file_db_factory() as session:
                assert (await ConfigService(session).get_provider_config("grok"))["audio_max_workers"] == "2"
    finally:
        official_sync.reset_for_tests()
