"""推荐变化的提示接口：读取提示、保留当前设置或改用推荐模型，使用真实数据库。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.catalog import query as catalog
from lib.config.recommended_models import FROZEN_SETTING_KEY, seed_provider_recommendations
from lib.config.service import ConfigService
from lib.db import get_async_session
from lib.db.repositories.credential_repository import CredentialRepository
from lib.i18n.zh import errors as zh_errors
from server.error_handlers import register_error_handlers
from server.routers import system_config
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.catalog_support import install_catalog, with_recommendation

NEXT_IMAGE = "doubao-seedream-4-5-251128"


@pytest.fixture
def recommendation_client(db_factory) -> Iterator[TestClient]:
    app = FastAPI()
    override_auth(app)
    register_error_handlers(app)

    async def _session():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session
    app.include_router(system_config.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def changed_recommendation(db_factory, monkeypatch) -> str:
    """ark 首次配置时写入推荐，之后目录把推荐图片模型换成 ``NEXT_IMAGE``；返回原来的全局默认图片模型。"""
    async with db_factory() as session:
        await ConfigService(session).set_setting(FROZEN_SETTING_KEY, "1")
        await CredentialRepository(session).create(provider="ark", name="主 Key", api_key="TEST-ark")
        await seed_provider_recommendations(session, "ark")
        await session.commit()
    before = f"ark/{catalog.default_model('ark', 'image')}"
    install_catalog(monkeypatch, with_recommendation(catalog.factory_snapshot(), "ark", "image", NEXT_IMAGE))
    return before


async def _global_image_default(db_factory) -> str:
    async with db_factory() as session:
        return await ConfigService(session).get_setting("default_image_backend")


async def test_lists_the_changed_recommendation(recommendation_client, changed_recommendation):
    response = recommendation_client.get("/api/v1/system/config/recommendation-changes")

    assert response.status_code == 200
    assert response.json() == {
        "changes": [
            {
                "media_type": "image",
                "provider_id": "ark",
                "recommended": f"ark/{NEXT_IMAGE}",
                "current": changed_recommendation,
            }
        ]
    }


@pytest.mark.parametrize("apply", [False, True])
async def test_acknowledging_clears_the_notice(recommendation_client, db_factory, changed_recommendation, apply):
    response = recommendation_client.post(
        "/api/v1/system/config/recommendation-changes/acknowledge",
        json={"media_type": "image", "recommended": f"ark/{NEXT_IMAGE}", "apply": apply},
    )

    assert response.status_code == 204
    assert recommendation_client.get("/api/v1/system/config/recommendation-changes").json() == {"changes": []}
    expected = f"ark/{NEXT_IMAGE}" if apply else changed_recommendation
    assert await _global_image_default(db_factory) == expected


async def test_a_stale_recommendation_is_rejected(recommendation_client, db_factory, changed_recommendation):
    response = recommendation_client.post(
        "/api/v1/system/config/recommendation-changes/acknowledge",
        json={"media_type": "image", "recommended": "ark/doubao-seedream-4-0-250828", "apply": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == zh_errors.MESSAGES["recommendation_changed_again"]
    assert await _global_image_default(db_factory) == changed_recommendation
    assert len(recommendation_client.get("/api/v1/system/config/recommendation-changes").json()["changes"]) == 1
