"""供应商倍率接口：GET / PUT /api/v1/providers/{provider_id}/multiplier，以及通用配置接口对倍率键的拒绝，绑内存库。"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db import get_async_session
from lib.db.models.custom_provider import CustomProvider
from server.routers import providers
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth


@pytest.fixture
def multiplier_client(session_factory: async_sessionmaker[AsyncSession]) -> Generator[TestClient]:
    app = FastAPI()

    async def _override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _override_session
    override_auth(app)
    app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    with TestClient(app) as test_client:
        yield test_client


def test_builtin_multiplier_defaults_set_and_reset(multiplier_client: TestClient) -> None:
    # 订阅制的 ark-agent-plan 默认 0，其余默认 1
    assert multiplier_client.get("/api/v1/providers/ark-agent-plan/multiplier").json() == {
        "multiplier": 0.0,
        "default": 0.0,
    }
    assert multiplier_client.get("/api/v1/providers/openai/multiplier").json() == {"multiplier": 1.0, "default": 1.0}

    resp = multiplier_client.put("/api/v1/providers/openai/multiplier", json={"multiplier": 0.8})
    assert resp.status_code == 200
    assert resp.json() == {"multiplier": 0.8, "default": 1.0}
    assert multiplier_client.get("/api/v1/providers/openai/multiplier").json()["multiplier"] == 0.8

    # null 恢复默认值
    resp = multiplier_client.put("/api/v1/providers/openai/multiplier", json={"multiplier": None})
    assert resp.json() == {"multiplier": 1.0, "default": 1.0}


async def test_custom_provider_multiplier_requires_existing_provider_and_valid_value(
    multiplier_client: TestClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        provider = CustomProvider(
            display_name="中转站", discovery_format="openai", base_url="http://localhost:9", api_key="k"
        )
        session.add(provider)
        await session.commit()
        provider_id = f"custom-{provider.id}"

    assert multiplier_client.put(f"/api/v1/providers/{provider_id}/multiplier", json={"multiplier": 1.5}).json() == {
        "multiplier": 1.5,
        "default": 1.0,
    }
    assert (
        multiplier_client.put(f"/api/v1/providers/{provider_id}/multiplier", json={"multiplier": -1}).status_code == 422
    )
    assert multiplier_client.get("/api/v1/providers/custom-999999/multiplier").status_code == 404
    assert multiplier_client.get("/api/v1/providers/nope/multiplier").status_code == 404


@pytest.mark.parametrize("value", ["0.5", "-1", None])
def test_generic_config_patch_rejects_multiplier_key(multiplier_client: TestClient, value: str | None) -> None:
    # 倍率只经专用接口修改：通用配置接口不论取值合法与否都拒绝，已设置的倍率不变
    multiplier_client.put("/api/v1/providers/openai/multiplier", json={"multiplier": 0.8})

    resp = multiplier_client.patch("/api/v1/providers/openai/config", json={"price_multiplier": value})

    assert resp.status_code == 422
    assert multiplier_client.get("/api/v1/providers/openai/multiplier").json()["multiplier"] == 0.8


async def test_comfyui_provider_defaults_to_zero(
    multiplier_client: TestClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """ComfyUI 协议的供应商跑在用户自己的机器上，默认倍率 0；按租用算力计价时可以改成非 0。"""
    async with session_factory() as session:
        provider = CustomProvider(
            display_name="ComfyUI", discovery_format="comfyui", base_url="http://localhost:8188", api_key=""
        )
        session.add(provider)
        await session.commit()
        provider_id = f"custom-{provider.id}"

    assert multiplier_client.get(f"/api/v1/providers/{provider_id}/multiplier").json() == {
        "multiplier": 0.0,
        "default": 0.0,
    }
    assert multiplier_client.put(f"/api/v1/providers/{provider_id}/multiplier", json={"multiplier": 1}).json() == {
        "multiplier": 1.0,
        "default": 0.0,
    }
