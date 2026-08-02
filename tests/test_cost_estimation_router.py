"""Tests for cost estimation router."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import cost_estimation
from tests.auth_deps import AUTH_DEPENDENCIES


def _make_app():
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(cost_estimation.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return app


def _mock_pm(**overrides):
    """Create a mock to replace the ``get_project_manager()`` singleton getter."""
    mock = MagicMock()
    for k, v in overrides.items():
        setattr(mock, k, MagicMock(return_value=v))
    return mock


class TestCostEstimationRouter:
    def test_project_not_found_returns_404(self):
        with patch.object(cost_estimation, "get_project_manager", lambda: _mock_pm(project_exists=False)):
            with TestClient(_make_app()) as client:
                resp = client.get("/api/v1/projects/nonexistent/cost-estimate")
        assert resp.status_code == 404
        assert "不存在" in resp.json()["detail"]

    def test_success_returns_correct_structure(self):
        fake_result = {
            "project_name": "demo",
            "models": {
                "image": {"provider": "gemini", "model": "m"},
                "video": {"provider": "gemini", "model": "m"},
            },
            "episodes": [],
            "project_totals": {"estimate": {}, "actual": {}},
        }

        mock_pm = _mock_pm(project_exists=True, load_project={"episodes": []})

        with (
            patch.object(cost_estimation, "get_project_manager", lambda: mock_pm),
            patch.object(cost_estimation, "CostEstimationService") as MockService,
        ):
            MockService.return_value.compute = AsyncMock(return_value=fake_result)

            with TestClient(_make_app()) as client:
                resp = client.get("/api/v1/projects/demo/cost-estimate")

        assert resp.status_code == 200
        body = resp.json()
        assert body["project_name"] == "demo"
        assert "models" in body
        assert "episodes" in body
        assert "project_totals" in body

    def test_no_auth_returns_401(self, monkeypatch):
        # AUTH_ENABLED=false 时 get_current_user 直接返回匿名 admin，这里就测不到拒绝。
        monkeypatch.setenv("AUTH_ENABLED", "true")
        app = FastAPI()
        register_error_handlers(app)
        # Do NOT override the auth dependency — real auth should reject.
        # 认证依赖挂在注册处，这里须与 server/app.py 的挂法一致。
        app.include_router(cost_estimation.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/v1/projects/demo/cost-estimate")
        assert resp.status_code == 401
