"""供应商模型接口：列出、启用与停用、按 ID 添加与移除，绑内存库。"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.catalog import query as catalog
from lib.config.service import ConfigService
from lib.db import get_async_session
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.routers import provider_models
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth


@pytest.fixture
def models_client(db_factory: async_sessionmaker[AsyncSession]) -> Generator[TestClient]:
    app = FastAPI()

    async def _override_session():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _override_session
    override_auth(app)
    app.include_router(provider_models.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    with TestClient(app) as test_client:
        yield test_client
    # 添加与移除会换掉目录查询里进程级的用户添加模型
    catalog.install_user_added_models(())


def _models(client: TestClient, provider_id: str) -> dict[str, dict]:
    resp = client.get(f"/api/v1/providers/{provider_id}/models")
    assert resp.status_code == 200
    return {item["model_id"]: item for item in resp.json()["models"]}


async def _disabled(db_factory: async_sessionmaker[AsyncSession], provider_id: str) -> frozenset[str]:
    async with db_factory() as session:
        statuses = await ConfigService(session).get_all_providers_status()
    return next(s.disabled_models for s in statuses if s.name == provider_id)


def test_builtin_list_has_state_flags_and_price_summary(models_client: TestClient) -> None:
    models = _models(models_client, "agnes")

    default = models["agnes-3.0-flash"]
    assert (default["enabled"], default["is_default"], default["official"], default["flags"]) == (
        True,
        True,
        True,
        [],
    )
    assert default["price"] == {
        "currency": "USD",
        "items": [
            {"meter": "uncached_input_tokens", "min_price": 0.05, "max_price": 0.05, "per": 1_000_000},
            {"meter": "output_tokens", "min_price": 0.15, "max_price": 0.15, "per": 1_000_000},
        ],
    }
    # 条目已下架的官方收录模型：没有状态行时为停用
    legacy = models["agnes-2.0-flash"]
    assert (legacy["enabled"], legacy["is_default"]) == (False, False)
    assert "discontinued" in legacy["flags"]
    assert models_client.get("/api/v1/providers/nope/models").status_code == 404


async def test_disabling_default_model_takes_effect_immediately(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    resp = models_client.put(
        "/api/v1/providers/agnes/models/enabled", json={"model_id": "agnes-3.0-flash", "enabled": False}
    )

    assert resp.status_code == 200
    assert (resp.json()["enabled"], resp.json()["is_default"]) == (False, False)
    assert "agnes-3.0-flash" in await _disabled(db_factory, "agnes")

    models_client.put("/api/v1/providers/agnes/models/enabled", json={"model_id": "agnes-2.0-flash", "enabled": True})
    models = _models(models_client, "agnes")
    assert models["agnes-2.0-flash"]["enabled"] is True
    assert "agnes-2.0-flash" not in await _disabled(db_factory, "agnes")
    missing = models_client.put("/api/v1/providers/agnes/models/enabled", json={"model_id": "nope", "enabled": True})
    assert missing.status_code == 404


def test_add_by_id_matches_listing_exactly_and_by_media_type(models_client: TestClient) -> None:
    # 两栖模型：同一 API 模型名有图片与视频两个供应商模型，要给媒体类型
    assert models_client.post("/api/v1/providers/kling/models", json={"model_id": "kling-v3-omni"}).status_code == 422

    models_client.put(
        "/api/v1/providers/kling/models/enabled", json={"model_id": "kling-v3-omni-image", "enabled": False}
    )
    resp = models_client.post(
        "/api/v1/providers/kling/models", json={"model_id": "kling-v3-omni", "media_type": "image"}
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["official"] is True
    assert (body["model"]["model_id"], body["model"]["enabled"]) == ("kling-v3-omni-image", True)


def test_add_unlisted_model_as_user_added_then_remove(models_client: TestClient) -> None:
    resp = models_client.post("/api/v1/providers/ark/models", json={"model_id": "my-private-ep", "media_type": "text"})

    assert resp.status_code == 201
    added = resp.json()
    assert added["official"] is False
    assert (added["model"]["display_name"], added["model"]["flags"]) == (None, ["unpriced"])
    # 用户添加的模型并进目录查询：设置里引用它可以解析
    assert catalog.get_model("ark", "my-private-ep", media_type="text") is not None
    assert models_client.post("/api/v1/providers/ark/models", json={"model_id": "my-private-ep"}).status_code == 422

    assert models_client.delete("/api/v1/providers/ark/models/my-private-ep").status_code == 204
    assert "my-private-ep" not in _models(models_client, "ark")
    assert catalog.get_model("ark", "my-private-ep") is None


def test_user_added_model_matches_entry_by_name(models_client: TestClient) -> None:
    resp = models_client.post("/api/v1/providers/grok/models", json={"model_id": "google/gemini-3-flash-preview"})

    assert resp.status_code == 201
    model = resp.json()["model"]
    assert (model["media_type"], model["display_name"], model["flags"]) == ("text", "Gemini 3 Flash", [])
    assert model["price"]["currency"] == "USD"


def test_official_model_cannot_be_removed_and_unknown_type_needs_media_type(models_client: TestClient) -> None:
    assert models_client.delete("/api/v1/providers/agnes/models/agnes-3.0-flash").status_code == 422
    assert models_client.delete("/api/v1/providers/agnes/models/nope").status_code == 404
    # 对应不上条目又没给媒体类型
    assert models_client.post("/api/v1/providers/ark/models", json={"model_id": "mystery-model"}).status_code == 422
    # 供应商不提供这类模型
    resp = models_client.post(
        "/api/v1/providers/openai/models", json={"model_id": "mystery-clip", "media_type": "video"}
    )
    assert resp.status_code == 422


async def test_custom_provider_list_and_disable_clears_default(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with db_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转站",
            discovery_format="openai",
            base_url="https://relay.example.com/v1",
            api_key="k",
            models=[
                {
                    "model_id": "anthropic/gemini-3-flash-preview",
                    "display_name": "anthropic/gemini-3-flash-preview",
                    "endpoint": "openai-chat",
                    "is_default": True,
                    "is_enabled": True,
                },
                {"model_id": "house-model", "display_name": "自家模型", "endpoint": "openai-chat", "is_enabled": True},
            ],
        )
        await session.commit()
        provider_id = f"custom-{provider.id}"

    models = _models(models_client, provider_id)
    matched = models["anthropic/gemini-3-flash-preview"]
    assert (matched["display_name"], matched["is_default"], matched["endpoint"], matched["flags"]) == (
        "Gemini 3 Flash",
        True,
        "openai-chat",
        [],
    )
    house = models["house-model"]
    assert (house["display_name"], house["flags"], house["price"]) == ("自家模型", ["unpriced"], None)

    resp = models_client.put(
        f"/api/v1/providers/{provider_id}/models/enabled",
        json={"model_id": "anthropic/gemini-3-flash-preview", "enabled": False},
    )
    assert (resp.json()["enabled"], resp.json()["is_default"]) == (False, False)
    async with db_factory() as session:
        row = await CustomProviderRepository(session).get_model_by_ids(provider.id, "anthropic/gemini-3-flash-preview")
    assert row is not None
    assert (row.is_enabled, row.is_default) == (False, False)
    # 自定义供应商的模型经自定义供应商接口增删
    assert models_client.post(f"/api/v1/providers/{provider_id}/models", json={"model_id": "x"}).status_code == 404
