"""自定义供应商管理 API 测试。

通过 TestClient + dependency_overrides 测试 CRUD、模型管理、
模型发现和连接测试端点。使用内存 SQLite 数据库。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.config.service import ConfigService
from lib.db import get_async_session
from lib.db.base import Base
from server.auth import CurrentUserInfo, get_current_user
from server.routers import custom_providers

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_engine():
    """内存 SQLite 引擎。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture()
async def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture()
def app(session_factory) -> FastAPI:
    """创建绑定内存数据库的 FastAPI 应用。"""
    _app = FastAPI()

    async def _override_session():
        async with session_factory() as session:
            yield session

    _app.dependency_overrides[get_async_session] = _override_session
    _app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    _app.include_router(custom_providers.router, prefix="/api/v1")
    return _app


@pytest.fixture()
async def session(session_factory) -> AsyncSession:
    async with session_factory() as s:
        yield s


@pytest.fixture()
def client(app) -> TestClient:
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Provider CRUD
# ---------------------------------------------------------------------------


class TestCreateProvider:
    def test_returns_201(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Test Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key-12345678",
                "models": [
                    {
                        "model_id": "gpt-4",
                        "display_name": "GPT-4",
                        "endpoint": "openai-chat",
                    }
                ],
            },
        )
        assert resp.status_code == 201

    def test_response_structure(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Test Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key-12345678",
                "models": [
                    {
                        "model_id": "gpt-4",
                        "display_name": "GPT-4",
                        "endpoint": "openai-chat",
                    }
                ],
            },
        )
        body = resp.json()
        assert body["display_name"] == "Test Provider"
        assert body["discovery_format"] == "openai"
        assert body["base_url"] == "https://api.example.com/v1"
        # api_key must be masked
        assert "sk-test-key-12345678" not in body["api_key_masked"]
        assert body["api_key_masked"].startswith("sk-t")
        assert len(body["models"]) == 1
        assert body["models"][0]["model_id"] == "gpt-4"
        assert "created_at" in body

    def test_create_without_models(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Empty Provider",
                "discovery_format": "google",
                "base_url": "https://api.example.com",
                "api_key": "AIza-test-12345678",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["models"] == []

    def test_create_openai_discovery_format_provider(self, client: TestClient):
        """回归: POST /custom-providers 接受 discovery_format=openai 且持久化正确字段。"""
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "OpenAI Gateway",
                "discovery_format": "openai",
                "base_url": "https://openai.example.com/v1",
                "api_key": "sk-openai-test-12345",
                "models": [
                    {
                        "model_id": "kling-v1",
                        "display_name": "Kling v1",
                        "endpoint": "newapi-video",
                        "is_default": True,
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["discovery_format"] == "openai"
        assert body["base_url"] == "https://openai.example.com/v1"
        assert len(body["models"]) == 1
        assert body["models"][0]["model_id"] == "kling-v1"


class TestListProviders:
    def test_empty_list(self, client: TestClient):
        resp = client.get("/api/v1/custom-providers")
        assert resp.status_code == 200
        assert resp.json() == {"providers": []}

    def test_lists_created_providers(self, client: TestClient):
        # Create two providers
        client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Provider A",
                "discovery_format": "openai",
                "base_url": "https://a.example.com/v1",
                "api_key": "sk-aaaa-key-12345678",
            },
        )
        client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Provider B",
                "discovery_format": "google",
                "base_url": "https://b.example.com",
                "api_key": "AIza-bbbb-12345678",
            },
        )
        resp = client.get("/api/v1/custom-providers")
        assert resp.status_code == 200
        body = resp.json()["providers"]
        assert len(body) == 2
        assert body[0]["display_name"] == "Provider A"
        assert body[1]["display_name"] == "Provider B"


class TestEndpointCatalog:
    """GET /endpoints 暴露 ENDPOINT_REGISTRY 作为前端单一真相源。"""

    def test_lists_all_six_endpoints(self, client: TestClient):
        resp = client.get("/api/v1/custom-providers/endpoints")
        assert resp.status_code == 200
        body = resp.json()
        keys = {e["key"] for e in body["endpoints"]}
        assert keys == {
            "openai-chat",
            "gemini-generate",
            "openai-images",
            "gemini-image",
            "openai-video",
            "newapi-video",
        }

    def test_descriptor_shape(self, client: TestClient):
        resp = client.get("/api/v1/custom-providers/endpoints")
        assert resp.status_code == 200
        for entry in resp.json()["endpoints"]:
            assert set(entry.keys()) == {
                "key",
                "media_type",
                "family",
                "display_name_key",
                "request_method",
                "request_path_template",
            }
            assert entry["request_method"] == "POST"
            assert entry["request_path_template"].startswith("/")

    def test_endpoint_route_not_shadowed_by_provider_id(self, client: TestClient):
        """回归：/endpoints 必须先于 /{provider_id} 注册，不能被解析为整型 provider_id。"""
        resp = client.get("/api/v1/custom-providers/endpoints")
        assert resp.status_code == 200, resp.text


class TestGetProvider:
    def test_returns_provider(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "My Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-get-test-12345678",
                "models": [
                    {
                        "model_id": "gpt-4o",
                        "display_name": "GPT-4o",
                        "endpoint": "openai-chat",
                    }
                ],
            },
        )
        pid = create_resp.json()["id"]
        resp = client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "My Provider"
        assert len(body["models"]) == 1

    def test_returns_404_for_nonexistent(self, client: TestClient):
        resp = client.get("/api/v1/custom-providers/9999")
        assert resp.status_code == 404


class TestUpdateProvider:
    def test_update_display_name(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Old Name",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-update-test-1234",
            },
        )
        pid = create_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/custom-providers/{pid}",
            json={"display_name": "New Name"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "New Name"

    def test_update_api_key_is_masked(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Key Test",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-old-key-12345678",
            },
        )
        pid = create_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/custom-providers/{pid}",
            json={"api_key": "sk-new-key-87654321"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "sk-new-key-87654321" not in body["api_key_masked"]
        assert body["api_key_masked"].startswith("sk-n")

    def test_returns_404_for_nonexistent(self, client: TestClient):
        resp = client.patch(
            "/api/v1/custom-providers/9999",
            json={"display_name": "Nope"},
        )
        assert resp.status_code == 404

    def test_returns_400_for_empty_body(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Empty Update",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-empty-test-1234",
            },
        )
        pid = create_resp.json()["id"]
        resp = client.patch(f"/api/v1/custom-providers/{pid}", json={})
        assert resp.status_code == 400


class TestDeleteProvider:
    def test_delete_existing(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "To Delete",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-delete-key-1234",
            },
        )
        pid = create_resp.json()["id"]
        resp = client.delete(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 204

        # Verify it's gone
        get_resp = client.get(f"/api/v1/custom-providers/{pid}")
        assert get_resp.status_code == 404

    def test_returns_404_for_nonexistent(self, client: TestClient):
        resp = client.delete("/api/v1/custom-providers/9999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------


class TestReplaceModels:
    def test_replace_entire_model_list(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Model Test",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-model-test-1234",
                "models": [
                    {
                        "model_id": "old-model",
                        "display_name": "Old Model",
                        "endpoint": "openai-chat",
                    }
                ],
            },
        )
        pid = create_resp.json()["id"]

        new_models = [
            {
                "model_id": "new-text",
                "display_name": "New Text Model",
                "endpoint": "openai-chat",
                "is_default": True,
            },
            {
                "model_id": "new-image",
                "display_name": "New Image Model",
                "endpoint": "openai-images",
                "is_default": True,
            },
        ]
        resp = client.put(f"/api/v1/custom-providers/{pid}/models", json={"models": new_models})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert {m["model_id"] for m in body} == {"new-text", "new-image"}

    def test_returns_404_for_nonexistent_provider(self, client: TestClient):
        resp = client.put("/api/v1/custom-providers/9999/models", json={"models": []})
        assert resp.status_code == 404

    def test_verify_old_models_removed(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Replace Verify",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-replace-test-12",
                "models": [
                    {
                        "model_id": "original",
                        "display_name": "Original",
                        "endpoint": "openai-chat",
                    }
                ],
            },
        )
        pid = create_resp.json()["id"]

        client.put(
            f"/api/v1/custom-providers/{pid}/models",
            json={
                "models": [
                    {
                        "model_id": "replacement",
                        "display_name": "Replacement",
                        "endpoint": "newapi-video",
                    }
                ]
            },
        )

        # Verify via get provider
        get_resp = client.get(f"/api/v1/custom-providers/{pid}")
        models = get_resp.json()["models"]
        assert len(models) == 1
        assert models[0]["model_id"] == "replacement"


# ---------------------------------------------------------------------------
# Discover models (mock)
# ---------------------------------------------------------------------------


class TestDiscoverModels:
    def test_discover_openai(self, client: TestClient):
        fake_models = [
            {
                "model_id": "gpt-4",
                "display_name": "gpt-4",
                "endpoint": "openai-chat",
                "is_default": True,
                "is_enabled": True,
            },
        ]
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            return_value=fake_models,
        ):
            resp = client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "discovery_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-discover-test",
                },
            )
        assert resp.status_code == 200
        assert len(resp.json()["models"]) == 1
        assert resp.json()["models"][0]["model_id"] == "gpt-4"

    def test_discover_google(self, client: TestClient):
        """google discovery_format 透传到 discover_models。"""
        fake_models = [
            {
                "model_id": "gemini-2.0-flash",
                "display_name": "gemini-2.0-flash",
                "endpoint": "gemini-generate",
                "is_default": True,
                "is_enabled": True,
            },
        ]
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            return_value=fake_models,
        ) as mock_discover:
            resp = client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "discovery_format": "google",
                    "base_url": "https://generativelanguage.googleapis.com",
                    "api_key": "AIza-google-discover",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["models"][0]["model_id"] == "gemini-2.0-flash"
        # 确认 discovery_format 透传
        assert mock_discover.call_args.kwargs["discovery_format"] == "google"

    def test_discover_invalid_format(self, client: TestClient):
        """discover_models 抛 ValueError 时返回 400。"""
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            side_effect=ValueError("不支持的 discovery_format: 'unknown'"),
        ):
            resp = client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "discovery_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-test",
                },
            )
        assert resp.status_code == 400

    def test_discover_api_failure(self, client: TestClient):
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection refused"),
        ):
            resp = client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "discovery_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-test",
                },
            )
        assert resp.status_code == 502


class TestDiscoverModelsByStoredProvider:
    """回归: 编辑已保存供应商时，前端无法重新提交明文 api_key，需用 stored 凭证调用 by-id 端点。"""

    def _create(self, client: TestClient) -> int:
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Stored Cred Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-stored-discover-1234",
            },
        )
        return resp.json()["id"]

    def test_uses_stored_credentials(self, client: TestClient):
        """by-id discover 应把 stored discovery_format/base_url/api_key 透传到 discover_models。"""
        pid = self._create(client)
        fake_models = [
            {
                "model_id": "gpt-4",
                "display_name": "gpt-4",
                "endpoint": "openai-chat",
                "is_default": True,
                "is_enabled": True,
            }
        ]
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            return_value=fake_models,
        ) as mock_discover:
            resp = client.post(f"/api/v1/custom-providers/{pid}/discover")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["model_id"] == "gpt-4"
        # 验证 stored 凭证被透传（明文 api_key，不是 mask 后的）
        kwargs = mock_discover.call_args.kwargs
        assert kwargs["discovery_format"] == "openai"
        assert kwargs["base_url"] == "https://api.example.com/v1"
        assert kwargs["api_key"] == "sk-stored-discover-1234"

    def test_returns_404_for_nonexistent(self, client: TestClient):
        resp = client.post("/api/v1/custom-providers/9999/discover")
        assert resp.status_code == 404

    def test_upstream_failure_returns_502(self, client: TestClient):
        pid = self._create(client)
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection refused"),
        ):
            resp = client.post(f"/api/v1/custom-providers/{pid}/discover")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Connection test (mock)
# ---------------------------------------------------------------------------


class TestConnectionTest:
    def test_openai_success(self, client: TestClient):
        with patch(
            "server.routers.custom_providers._test_openai",
            return_value=custom_providers.ConnectionTestResponse(success=True, message="连接成功", model_count=5),
        ):
            resp = client.post(
                "/api/v1/custom-providers/test",
                json={
                    "discovery_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-conn-test",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["model_count"] == 5

    def test_google_success(self, client: TestClient):
        with patch(
            "server.routers.custom_providers._test_google",
            return_value=custom_providers.ConnectionTestResponse(success=True, message="连接成功", model_count=10),
        ):
            resp = client.post(
                "/api/v1/custom-providers/test",
                json={
                    "discovery_format": "google",
                    "base_url": "https://api.example.com",
                    "api_key": "AIza-test",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["model_count"] == 10

    def test_openai_routes_to_test_openai(self, client: TestClient):
        """discovery_format=openai 应路由到 _test_openai。"""
        with patch(
            "server.routers.custom_providers._test_openai",
            return_value=custom_providers.ConnectionTestResponse(success=True, message="连接成功", model_count=42),
        ) as mock_openai_test:
            resp = client.post(
                "/api/v1/custom-providers/test",
                json={
                    "discovery_format": "openai",
                    "base_url": "https://openai.example.com/v1",
                    "api_key": "sk-openai-conn",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["model_count"] == 42
        mock_openai_test.assert_called_once()

    def test_unsupported_format_returns_false(self, client: TestClient):
        """不支持的 discovery_format 应返回 success=False。"""
        resp = client.post(
            "/api/v1/custom-providers/test",
            json={
                "discovery_format": "unsupported",
                "base_url": "https://api.example.com",
                "api_key": "test",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False

    def test_connection_failure(self, client: TestClient):
        with patch(
            "server.routers.custom_providers._test_openai",
            side_effect=RuntimeError("Connection refused"),
        ):
            resp = client.post(
                "/api/v1/custom-providers/test",
                json={
                    "discovery_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-fail-test",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "Connection refused" in body["message"]


# ---------------------------------------------------------------------------
# 回归测试：修复过的高危 bug
# ---------------------------------------------------------------------------

_PROVIDER_PAYLOAD = {
    "display_name": "Regression Test",
    "discovery_format": "openai",
    "base_url": "https://api.example.com/v1",
    "api_key": "sk-regression-1234",
    "models": [
        {
            "model_id": "gpt-4o",
            "display_name": "GPT-4o",
            "endpoint": "openai-chat",
            "is_default": True,
            "is_enabled": True,
        },
        {
            "model_id": "dall-e-3",
            "display_name": "DALL-E 3",
            "endpoint": "openai-images",
            "is_default": True,
            "is_enabled": True,
        },
    ],
}


class TestDeleteProviderCleansGlobalSettings:
    """回归: 删除 provider 时应清理全局 DB 中引用该 provider 的 default_*_backend。"""

    async def test_global_settings_cleaned_on_delete(self, client: TestClient, session: AsyncSession):
        # 创建供应商
        resp = client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = resp.json()["id"]

        # 模拟全局配置引用该供应商
        svc = ConfigService(session)
        await svc.set_setting("default_text_backend", f"custom-{pid}/gpt-4o")
        await svc.set_setting("default_image_backend", f"custom-{pid}/dall-e-3")
        await svc.set_setting("default_video_backend", "gemini-aistudio/veo-3")  # 不应被清理
        await session.commit()

        # 删除供应商（mock 掉项目清理和缓存失效）
        with (
            patch("server.routers.custom_providers._cleanup_project_refs"),
            patch("server.routers.custom_providers._invalidate_caches", new_callable=AsyncMock),
        ):
            del_resp = client.delete(f"/api/v1/custom-providers/{pid}")
        assert del_resp.status_code == 204

        # 验证引用被清理
        assert await svc.get_setting("default_text_backend", "") == ""
        assert await svc.get_setting("default_image_backend", "") == ""
        # 不相关的设置应保留
        assert await svc.get_setting("default_video_backend", "") == "gemini-aistudio/veo-3"


class TestDeleteProviderCleansProjectRefs:
    """回归: 删除 provider 时应清理项目级 project.json 中的悬空引用。"""

    def test_project_refs_cleaned_on_delete(self, client: TestClient):
        resp = client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = resp.json()["id"]
        prefix = f"custom-{pid}/"

        # 模拟 ProjectManager
        mock_pm = MagicMock()
        mock_pm.list_projects.return_value = ["project-a"]
        project_data = {"text_backend_script": f"{prefix}gpt-4o", "title": "Test"}
        mock_pm.load_project.return_value = project_data

        with (
            patch("lib.config.resolver.get_project_manager", return_value=mock_pm),
            patch("server.routers.custom_providers._invalidate_caches", new_callable=AsyncMock),
        ):
            del_resp = client.delete(f"/api/v1/custom-providers/{pid}")
        assert del_resp.status_code == 204

        # 验证 update_project 被调用来清理引用
        mock_pm.update_project.assert_called_once()
        call_args = mock_pm.update_project.call_args
        assert call_args[0][0] == "project-a"
        # 执行 mutate_fn 验证清理逻辑
        mutate_fn = call_args[0][1]
        test_proj = {"text_backend_script": f"{prefix}gpt-4o", "title": "Test"}
        mutate_fn(test_proj)
        assert "text_backend_script" not in test_proj
        assert test_proj["title"] == "Test"  # 无关字段保留


class TestReplaceModelsCleansStaleRefs:
    """回归: 替换 models 时应清理引用已删除 model 的全局配置。"""

    async def test_stale_model_refs_cleaned(self, client: TestClient, session: AsyncSession):
        resp = client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = resp.json()["id"]

        # 模拟全局配置引用 gpt-4o
        svc = ConfigService(session)
        await svc.set_setting("default_text_backend", f"custom-{pid}/gpt-4o")
        await session.commit()

        # 替换 models — 移除 gpt-4o，保留 dall-e-3
        with patch("server.routers.custom_providers._invalidate_caches", new_callable=AsyncMock):
            replace_resp = client.put(
                f"/api/v1/custom-providers/{pid}/models",
                json={
                    "models": [
                        {
                            "model_id": "dall-e-3",
                            "display_name": "DALL-E 3",
                            "endpoint": "openai-images",
                            "is_default": True,
                            "is_enabled": True,
                        },
                    ]
                },
            )
        assert replace_resp.status_code == 200

        # gpt-4o 被删除，引用它的全局配置应被清空
        assert await svc.get_setting("default_text_backend", "") == ""


class TestEmptyModelIdRejected:
    """回归: 启用模型必须有非空 model_id。"""

    def test_create_with_empty_model_id(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Bad Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-bad",
                "models": [
                    {"model_id": "", "display_name": "Empty", "endpoint": "openai-chat", "is_enabled": True},
                ],
            },
        )
        assert resp.status_code == 422

    def test_replace_models_with_empty_model_id(self, client: TestClient):
        create_resp = client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = create_resp.json()["id"]
        with patch("server.routers.custom_providers._invalidate_caches", new_callable=AsyncMock):
            resp = client.put(
                f"/api/v1/custom-providers/{pid}/models",
                json={
                    "models": [
                        {"model_id": "  ", "display_name": "Blank", "endpoint": "openai-chat", "is_enabled": True},
                    ]
                },
            )
        assert resp.status_code == 422


class TestUnknownEndpointRejected:
    """回归：写入路径用未注册 endpoint key 应被 AfterValidator 拦下，返回 422。"""

    def test_create_with_unknown_endpoint(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Unknown Endpoint",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-key",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M",
                        "endpoint": "anthropic-messages",
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 422
        assert "unknown endpoint" in resp.text


class TestDuplicateModelIdRejected:
    """回归: 同一供应商下不允许重复 model_id。"""

    def test_create_with_duplicate(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Dup Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-dup",
                "models": [
                    {"model_id": "m1", "display_name": "M1a", "endpoint": "openai-chat", "is_enabled": True},
                    {"model_id": "m1", "display_name": "M1b", "endpoint": "openai-chat", "is_enabled": True},
                ],
            },
        )
        assert resp.status_code == 422
        assert "重复" in resp.json()["detail"]


class TestFullUpdateProvider:
    """回归: PUT 全量更新端点应原子更新 provider + models。"""

    def test_full_update(self, client: TestClient):
        create_resp = client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = create_resp.json()["id"]
        with patch("server.routers.custom_providers._invalidate_caches", new_callable=AsyncMock):
            resp = client.put(
                f"/api/v1/custom-providers/{pid}",
                json={
                    "display_name": "Updated Name",
                    "base_url": "https://new-api.example.com/v1",
                    "models": [
                        {
                            "model_id": "new-model",
                            "display_name": "New",
                            "endpoint": "openai-chat",
                            "is_default": True,
                            "is_enabled": True,
                        },
                    ],
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "Updated Name"
        assert body["base_url"] == "https://new-api.example.com/v1"
        assert len(body["models"]) == 1
        assert body["models"][0]["model_id"] == "new-model"

    def test_full_update_rejects_empty_model_id(self, client: TestClient):
        create_resp = client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = create_resp.json()["id"]
        resp = client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "X",
                "base_url": "https://x.com",
                "models": [
                    {"model_id": "", "display_name": "Bad", "endpoint": "openai-chat", "is_enabled": True},
                ],
            },
        )
        assert resp.status_code == 422

    def test_full_update_404_for_nonexistent(self, client: TestClient):
        resp = client.put(
            "/api/v1/custom-providers/9999",
            json={
                "display_name": "X",
                "base_url": "https://x.com",
                "models": [],
            },
        )
        assert resp.status_code == 404


class TestValidateBackendValueCustomPrefix:
    """回归: validate_backend_value 应接受 custom-* 前缀。"""

    def test_custom_prefix_accepted(self):
        from server.routers._validators import validate_backend_value

        _t = lambda key, **kw: key  # noqa: E731
        # 不应抛异常
        validate_backend_value("custom-3/gpt-4o", "default_text_backend", _t)

    def test_unknown_provider_rejected(self):
        from fastapi import HTTPException

        from server.routers._validators import validate_backend_value

        _t = lambda key, **kw: key  # noqa: E731
        with pytest.raises(HTTPException) as exc_info:
            validate_backend_value("nonexistent/model", "default_text_backend", _t)
        assert exc_info.value.status_code == 400


class TestDuplicateDefaultRejected:
    """回归: 同一 media_type 下最多只能有一个 is_default=True 的模型。"""

    def test_create_with_duplicate_defaults(self, client: TestClient):
        """创建供应商时同一 media_type 有两个 is_default=true 的模型，期望 422。"""
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Dup Default Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-dup-default-1234",
                "models": [
                    {
                        "model_id": "text-a",
                        "display_name": "Text A",
                        "endpoint": "openai-chat",
                        "is_default": True,
                        "is_enabled": True,
                    },
                    {
                        "model_id": "text-b",
                        "display_name": "Text B",
                        "endpoint": "openai-chat",
                        "is_default": True,
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 422
        assert "默认模型" in resp.json()["detail"]

    def test_single_default_per_type_allowed(self, client: TestClient):
        """不同 media_type 各一个 default，期望 201 成功。"""
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Multi Default Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-multi-default-12",
                "models": [
                    {
                        "model_id": "text-model",
                        "display_name": "Text Model",
                        "endpoint": "openai-chat",
                        "is_default": True,
                        "is_enabled": True,
                    },
                    {
                        "model_id": "image-model",
                        "display_name": "Image Model",
                        "endpoint": "openai-images",
                        "is_default": True,
                        "is_enabled": True,
                    },
                    {
                        "model_id": "video-model",
                        "display_name": "Video Model",
                        "endpoint": "newapi-video",
                        "is_default": True,
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 201


class TestPriceFieldConsistency:
    """回归: price_output 不能脱离 price_input 单独存在；currency 可独立存在。"""

    def test_output_without_input_rejected(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Bad Price",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-price-test",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "endpoint": "openai-chat",
                        "is_enabled": True,
                        "price_output": 0.5,
                    },
                ],
            },
        )
        assert resp.status_code == 422

    def test_currency_without_input_accepted(self, client: TestClient):
        """currency 可独立存在（用户先选币种，稍后填价格）。"""
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Currency Only",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-price-test",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "endpoint": "openai-chat",
                        "is_enabled": True,
                        "currency": "USD",
                    },
                ],
            },
        )
        assert resp.status_code == 201

    def test_valid_price_fields_accepted(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Good Price",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-price-test",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "endpoint": "openai-chat",
                        "is_enabled": True,
                        "price_input": 0.1,
                        "price_output": 0.2,
                        "currency": "USD",
                    },
                ],
            },
        )
        assert resp.status_code == 201


class TestResolutionField:
    """验证 ModelInput / ModelResponse 的 resolution 字段贯通读写。"""

    def test_create_with_resolution_and_read_back(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "X",
                "discovery_format": "openai",
                "base_url": "https://api.example.com",
                "api_key": "k",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "endpoint": "newapi-video",
                        "is_default": True,
                        "is_enabled": True,
                        "resolution": "720p",
                    },
                ],
            },
        )
        assert resp.status_code == 201
        pid = resp.json()["id"]

        # 读取，确认 resolution 返回
        resp = client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        models = resp.json()["models"]
        assert len(models) == 1
        assert models[0]["resolution"] == "720p"

    def test_resolution_defaults_to_null_when_omitted(self, client: TestClient):
        """未指定 resolution 时应返回 None。"""
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Y",
                "discovery_format": "openai",
                "base_url": "https://api.example.com",
                "api_key": "k",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "endpoint": "newapi-video",
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 201
        pid = resp.json()["id"]

        resp = client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["resolution"] is None

    def test_replace_models_updates_resolution_to_null(self, client: TestClient):
        """通过 PUT /models 更新 resolution 为 null。"""
        # 先创建带 resolution 的 provider
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Z",
                "discovery_format": "openai",
                "base_url": "https://api.example.com",
                "api_key": "k",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "endpoint": "newapi-video",
                        "is_enabled": True,
                        "resolution": "1080p",
                    },
                ],
            },
        )
        assert resp.status_code == 201
        pid = resp.json()["id"]

        # 替换模型列表，resolution 省略即为 null
        resp = client.put(
            f"/api/v1/custom-providers/{pid}/models",
            json={
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "endpoint": "newapi-video",
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 200

        # 读取验证为 null
        resp = client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["resolution"] is None


# ---------------------------------------------------------------------------
# 新增 422 校验用例
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_provider_with_unknown_endpoint_returns_422(client):
    payload = {
        "display_name": "X",
        "discovery_format": "openai",
        "base_url": "https://x",
        "api_key": "k",
        "models": [
            {
                "model_id": "claude-4",
                "display_name": "Claude 4",
                "endpoint": "anthropic-messages",  # 非法
                "is_default": False,
                "is_enabled": True,
            }
        ],
    }
    resp = client.post("/api/v1/custom-providers", json=payload)
    assert resp.status_code == 422
    assert "unknown_endpoint" in resp.text or "anthropic-messages" in resp.text


@pytest.mark.asyncio
async def test_create_provider_unknown_discovery_format_returns_422(client):
    payload = {
        "display_name": "X",
        "discovery_format": "newapi",  # 已被剔除
        "base_url": "https://x",
        "api_key": "k",
        "models": [],
    }
    resp = client.post("/api/v1/custom-providers", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_default_conflict_grouped_by_endpoint_media(client):
    """两条 endpoint 不同但推算 media_type 相同的模型不能同时 is_default。"""
    payload = {
        "display_name": "X",
        "discovery_format": "openai",
        "base_url": "https://x",
        "api_key": "k",
        "models": [
            {
                "model_id": "gpt-4o",
                "display_name": "a",
                "endpoint": "openai-chat",
                "is_default": True,
                "is_enabled": True,
            },
            {
                "model_id": "gemini-2.5",
                "display_name": "b",
                "endpoint": "gemini-generate",
                "is_default": True,
                "is_enabled": True,
            },  # 都是 text → 冲突
        ],
    }
    resp = client.post("/api/v1/custom-providers", json=payload)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Anthropic discovery (智能体配置专用)
# ---------------------------------------------------------------------------


class TestDiscoverAnthropic:
    def test_explicit_credentials(self, client: TestClient):
        """显式传入 base_url + api_key，调用 _run_discover('anthropic', ...)。"""
        mock_models = [
            {"model_id": "claude-x", "display_name": "X", "endpoint": "", "is_default": False, "is_enabled": True}
        ]
        with patch("server.routers.custom_providers._run_discover", new=AsyncMock()) as mock_run:
            from server.routers.custom_providers import DiscoverResponse

            mock_run.return_value = DiscoverResponse(models=mock_models)

            resp = client.post(
                "/api/v1/custom-providers/discover-anthropic",
                json={"base_url": "https://example.com", "api_key": "sk-ant"},
            )

        assert resp.status_code == 200
        assert [m["model_id"] for m in resp.json()["models"]] == ["claude-x"]
        # 调用参数：discovery_format=anthropic，凭据透传
        args = mock_run.call_args.args
        assert args[0] == "anthropic"
        assert args[1] == "https://example.com"
        assert args[2] == "sk-ant"

    async def test_falls_back_to_stored_api_key(self, client: TestClient, session: AsyncSession):
        """请求未带 api_key 时，从 anthropic_api_key 设置 fallback。"""
        svc = ConfigService(session)
        await svc.set_setting("anthropic_api_key", "sk-stored")
        await svc.set_setting("anthropic_base_url", "https://stored.example")
        await session.commit()

        with patch("server.routers.custom_providers._run_discover", new=AsyncMock()) as mock_run:
            from server.routers.custom_providers import DiscoverResponse

            mock_run.return_value = DiscoverResponse(models=[])

            resp = client.post("/api/v1/custom-providers/discover-anthropic", json={})

        assert resp.status_code == 200
        args = mock_run.call_args.args
        assert args[1] == "https://stored.example"
        assert args[2] == "sk-stored"

    def test_returns_400_when_no_key_anywhere(self, client: TestClient):
        """请求未带 api_key 且 DB 也没有 → 400。"""
        resp = client.post("/api/v1/custom-providers/discover-anthropic", json={})
        assert resp.status_code == 400
        # i18n 默认 zh
        assert "API Key" in resp.json()["detail"]


class TestGetProviderCredentials:
    def test_returns_plaintext(self, client: TestClient):
        """正常路径返回明文 base_url + api_key。"""
        # 先创建 provider
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "OneAPI",
                "discovery_format": "openai",
                "base_url": "https://oneapi.example.com",
                "api_key": "sk-secret",
                "models": [],
            },
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/custom-providers/{provider_id}/credentials")
        assert resp.status_code == 200
        body = resp.json()
        assert body["base_url"] == "https://oneapi.example.com"
        assert body["api_key"] == "sk-secret"

    def test_returns_404_for_unknown_provider(self, client: TestClient):
        resp = client.get("/api/v1/custom-providers/99999/credentials")
        assert resp.status_code == 404
