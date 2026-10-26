"""自动选模预览接口使用真实配置解析器与数据库，不发供应商请求。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from server.routers import system_config
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth


async def test_auto_backends_returns_bucket_models_and_null_for_unavailable(db_factory, monkeypatch):
    async with db_factory() as session:
        provider = CustomProvider(
            display_name="Preview", discovery_format="openai", base_url="https://example.com", api_key="test"
        )
        session.add(provider)
        await session.flush()
        session.add(
            CustomProviderModel(
                provider_id=provider.id,
                model_id="image-model",
                display_name="Image",
                endpoint="openai-images-generations",
                is_default=True,
                is_enabled=True,
            )
        )
        await session.commit()
        provider_id = provider.provider_id

    monkeypatch.setattr(system_config, "async_session_factory", db_factory)
    app = FastAPI()
    override_auth(app)
    app.include_router(system_config.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    with TestClient(app) as client:
        response = client.get("/api/v1/system/config/auto-backends")
    assert response.status_code == 200
    assert response.json() == {
        "t2i": f"{provider_id}/image-model",
        "i2i": None,
        "i2v": None,
        "r2v": None,
        "text": None,
    }
