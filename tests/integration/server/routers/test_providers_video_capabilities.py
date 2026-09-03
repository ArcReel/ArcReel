"""providers 路由的无项目 video-capabilities 查询。"""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.config.resolver import VideoBucketCapabilityError
from lib.i18n.zh import errors as zh_errors
from server.error_handlers import register_error_handlers
from server.routers import providers
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth


def _client(monkeypatch, resolver_instance) -> TestClient:
    monkeypatch.setattr(providers, "ConfigResolver", lambda _factory: resolver_instance)
    app = FastAPI()
    override_auth(app)
    app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    return TestClient(app)


class TestGetModelVideoCapabilities:
    """GET /providers/video-capabilities"""

    def test_resolves_candidate_model_without_project(self, monkeypatch):
        """创建向导里项目尚不存在：按候选模型解析，project 传 None，约束上下文原样转交。"""
        resolver_instance = MagicMock()
        resolver_instance.video_capabilities_for_model = AsyncMock(return_value={"model": "candidate"})
        with _client(monkeypatch, resolver_instance) as client:
            resp = client.get(
                "/api/v1/providers/video-capabilities",
                params={"video_backend": "openai/sora-2", "resolution": "1080p", "uses_reference_images": "true"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"model": "candidate"}
        call = resolver_instance.video_capabilities_for_model.await_args
        assert call.args == ("openai", "sora-2", None)
        assert call.kwargs == {"resolution": "1080p", "uses_reference_images": True}

    def test_constraint_context_defaults(self, monkeypatch):
        """缺省不按分辨率收窄、不走参考图路径——无项目可回退，缺省即「无约束」。"""
        resolver_instance = MagicMock()
        resolver_instance.video_capabilities_for_model = AsyncMock(return_value={})
        with _client(monkeypatch, resolver_instance) as client:
            resp = client.get("/api/v1/providers/video-capabilities", params={"video_backend": "openai/sora-2"})
        assert resp.status_code == 200
        assert resolver_instance.video_capabilities_for_model.await_args.kwargs == {
            "resolution": None,
            "uses_reference_images": False,
        }

    def test_bare_provider_completes_default_model(self, monkeypatch):
        resolver_instance = MagicMock()
        resolver_instance.video_capabilities_for_model = AsyncMock(return_value={})
        with _client(monkeypatch, resolver_instance) as client:
            resp = client.get("/api/v1/providers/video-capabilities", params={"video_backend": "openai"})
        assert resp.status_code == 200
        provider_id, model_id, _ = resolver_instance.video_capabilities_for_model.await_args.args
        assert provider_id == "openai"
        assert model_id

    def test_video_backend_is_required(self, monkeypatch):
        with _client(monkeypatch, MagicMock()) as client:
            resp = client.get("/api/v1/providers/video-capabilities")
        assert resp.status_code == 422

    def test_malformed_video_backend_returns_400(self, monkeypatch):
        with _client(monkeypatch, MagicMock()) as client:
            resp = client.get("/api/v1/providers/video-capabilities", params={"video_backend": "no-such-provider"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == zh_errors.MESSAGES["video_backend_malformed"].format(value="no-such-provider")

    def test_resolver_value_error_returns_localized_422(self, monkeypatch):
        resolver_instance = MagicMock()
        resolver_instance.video_capabilities_for_model = AsyncMock(side_effect=ValueError("model not found"))
        with _client(monkeypatch, resolver_instance) as client:
            resp = client.get("/api/v1/providers/video-capabilities", params={"video_backend": "grok/unknown"})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "model not found" not in detail
        assert detail == zh_errors.MESSAGES["video_model_capabilities_unresolved"].format(value="grok/unknown")

    def test_capability_bucket_error_returns_localized_400(self, monkeypatch):
        resolver_instance = MagicMock()
        resolver_instance.video_capabilities_for_model = AsyncMock(
            side_effect=VideoBucketCapabilityError(
                code="video_capability_missing_r2v",
                capability="r2v",
                provider_id="kling",
                model_id="kling-v3",
                message="lacks r2v",
            )
        )
        with _client(monkeypatch, resolver_instance) as client:
            resp = client.get(
                "/api/v1/providers/video-capabilities",
                params={"video_backend": "kling/kling-v3", "uses_reference_images": "true"},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"] == zh_errors.MESSAGES["video_capability_missing_r2v"].format(
            provider="kling", model="kling-v3"
        )
