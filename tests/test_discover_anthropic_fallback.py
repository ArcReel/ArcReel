"""/custom-providers/discover-anthropic 回退到 active credential 的回归测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db import get_async_session
from lib.db.base import Base
from server.auth import CurrentUserInfo, get_current_user
from server.routers import agent_config, custom_providers


def _make_app(session_factory) -> FastAPI:
    app = FastAPI()

    async def override_session():
        async with session_factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_async_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(agent_config.router, prefix="/api/v1")
    app.include_router(custom_providers.router, prefix="/api/v1")
    return app


@pytest_asyncio.fixture
async def _engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def authed_client(_engine):
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    app = _make_app(factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_discover_falls_back_to_active_credential(authed_client, monkeypatch) -> None:
    captured: dict = {}

    async def fake_discover(discovery_format, base_url, api_key, _t):
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        return type("R", (), {"models": [], "errors": []})()

    monkeypatch.setattr("server.routers.custom_providers._run_discover", fake_discover)

    # Create an active credential first
    create_resp = await authed_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "deepseek", "api_key": "stored-sk"},
    )
    assert create_resp.status_code == 201, create_resp.text

    # /discover-anthropic with empty body → should pick up the active credential
    resp = await authed_client.post(
        "/api/v1/custom-providers/discover-anthropic",
        json={},
    )
    assert resp.status_code == 200, resp.text
    assert captured["api_key"] == "stored-sk"
    assert captured["base_url"] == "https://api.deepseek.com/anthropic"


@pytest.mark.asyncio
async def test_discover_no_active_no_body_returns_400(authed_client) -> None:
    resp = await authed_client.post(
        "/api/v1/custom-providers/discover-anthropic",
        json={},
    )
    assert resp.status_code == 400
