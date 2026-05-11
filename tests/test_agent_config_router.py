"""Agent config 路由测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db import get_async_session
from lib.db.base import Base
from server.auth import CurrentUserInfo, get_current_user
from server.routers import agent_config


def _make_app(session_factory) -> FastAPI:
    app = FastAPI()

    async def override_session():
        async with session_factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_async_session] = override_session
    app.include_router(agent_config.router, prefix="/api/v1")
    return app


@pytest_asyncio.fixture
async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def authed_client(_session_factory):
    app = _make_app(_session_factory)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def unauth_client(_session_factory):
    """No dependency override → real auth applies → expects 401/403."""
    app = _make_app(_session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── Task 9: GET /agent/preset-providers ───────────────────────────


@pytest.mark.asyncio
async def test_list_preset_providers_returns_catalog(authed_client) -> None:
    resp = await authed_client.get("/api/v1/agent/preset-providers")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert "custom_sentinel_id" in data
    assert data["custom_sentinel_id"] == "__custom__"
    ids = [p["id"] for p in data["providers"]]
    assert "deepseek" in ids
    assert "anthropic-official" in ids
    deepseek = next(p for p in data["providers"] if p["id"] == "deepseek")
    assert deepseek["messages_url"] == "https://api.deepseek.com/anthropic"
    assert deepseek["discovery_url"] == "https://api.deepseek.com"
    assert "default_model" in deepseek
    assert "icon_key" in deepseek


@pytest.mark.asyncio
async def test_list_preset_providers_requires_auth(unauth_client) -> None:
    resp = await unauth_client.get("/api/v1/agent/preset-providers")
    assert resp.status_code in (401, 403)


# ── Task 10: /agent/credentials CRUD ─────────────────────────────


@pytest.mark.asyncio
async def test_list_credentials_initially_empty(authed_client) -> None:
    resp = await authed_client.get("/api/v1/agent/credentials")
    assert resp.status_code == 200
    assert resp.json() == {"credentials": []}


@pytest.mark.asyncio
async def test_create_with_preset(authed_client) -> None:
    body = {"preset_id": "deepseek", "api_key": "sk-testkey12345"}
    resp = await authed_client.post("/api/v1/agent/credentials", json=body)
    assert resp.status_code == 201
    cred = resp.json()
    assert cred["preset_id"] == "deepseek"
    assert cred["base_url"] == "https://api.deepseek.com/anthropic"
    assert cred["model"] == "deepseek-chat"
    assert cred["display_name"] == "DeepSeek"
    assert cred["api_key_masked"].startswith("sk-")
    assert cred["icon_key"] == "DeepSeek"
    # 第一条凭证应自动 active
    assert cred["is_active"] is True


@pytest.mark.asyncio
async def test_create_custom_requires_base_url(authed_client) -> None:
    body = {"preset_id": "__custom__", "api_key": "sk"}
    resp = await authed_client.post("/api/v1/agent/credentials", json=body)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_custom_with_base_url(authed_client) -> None:
    body = {
        "preset_id": "__custom__",
        "display_name": "My Proxy",
        "base_url": "https://proxy.example.com/anthropic",
        "api_key": "sk",
        "model": "claude-sonnet-4",
    }
    resp = await authed_client.post("/api/v1/agent/credentials", json=body)
    assert resp.status_code == 201
    assert resp.json()["base_url"] == "https://proxy.example.com/anthropic"
    assert resp.json()["icon_key"] is None


@pytest.mark.asyncio
async def test_create_unknown_preset_rejected(authed_client) -> None:
    resp = await authed_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "nonexistent", "api_key": "sk"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_credential(authed_client) -> None:
    created = (
        await authed_client.post(
            "/api/v1/agent/credentials",
            json={"preset_id": "deepseek", "api_key": "sk1"},
        )
    ).json()
    cid = created["id"]
    resp = await authed_client.patch(
        f"/api/v1/agent/credentials/{cid}",
        json={"display_name": "Renamed", "api_key": "sk2"},
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Renamed"


@pytest.mark.asyncio
async def test_delete_active_blocked(authed_client) -> None:
    created = (
        await authed_client.post(
            "/api/v1/agent/credentials",
            json={"preset_id": "deepseek", "api_key": "sk"},
        )
    ).json()
    resp = await authed_client.delete(f"/api/v1/agent/credentials/{created['id']}")
    assert resp.status_code == 409
