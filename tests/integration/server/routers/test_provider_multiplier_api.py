"""In-memory provider multipliers GET/PUT /api/v1/providers/{provider_id}/multiplier and generic multiplier-key rejection."""

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
def multiplier_client(db_factory: async_sessionmaker[AsyncSession]) -> Generator[TestClient]:
    app = FastAPI()

    async def _override_session():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _override_session
    override_auth(app)
    app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    with TestClient(app) as test_client:
        yield test_client


def test_builtin_multiplier_defaults_set_and_reset(multiplier_client: TestClient) -> None:
    # Subscription ark-agent-plan defaults 0, others 1.
    assert multiplier_client.get("/api/v1/providers/ark-agent-plan/multiplier").json() == {
        "multiplier": 0.0,
        "default": 0.0,
    }
    assert multiplier_client.get("/api/v1/providers/openai/multiplier").json() == {"multiplier": 1.0, "default": 1.0}

    resp = multiplier_client.put("/api/v1/providers/openai/multiplier", json={"multiplier": 0.8})
    assert resp.status_code == 200
    assert resp.json() == {"multiplier": 0.8, "default": 1.0}
    assert multiplier_client.get("/api/v1/providers/openai/multiplier").json()["multiplier"] == 0.8

    # null restores defaults.
    resp = multiplier_client.put("/api/v1/providers/openai/multiplier", json={"multiplier": None})
    assert resp.json() == {"multiplier": 1.0, "default": 1.0}


async def test_custom_provider_multiplier_requires_existing_provider_and_valid_value(
    multiplier_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with db_factory() as session:
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
    # Multipliers change only through dedicated APIs; generic settings reject all values, preserving existing multipliers.
    multiplier_client.put("/api/v1/providers/openai/multiplier", json={"multiplier": 0.8})

    resp = multiplier_client.patch("/api/v1/providers/openai/config", json={"price_multiplier": value})

    assert resp.status_code == 422
    assert multiplier_client.get("/api/v1/providers/openai/multiplier").json()["multiplier"] == 0.8


async def test_comfyui_provider_defaults_to_zero(
    multiplier_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    """User-hosted ComfyUI defaults multiplier 0; rented-compute pricing may set nonzero."""
    async with db_factory() as session:
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
