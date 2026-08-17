"""Tests for cost estimation router."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import cost_estimation
from tests.auth_deps import AUTH_DEPENDENCIES

pytestmark = pytest.mark.unit


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

    def test_unit_quote_passes_delivery_choice_without_client_duration_facts(self):
        mock_pm = _mock_pm(
            project_exists=True,
            load_project={"episodes": [{"script_file": "ep1.json"}]},
            load_script={"video_units": [{"unit_id": "E1U1"}]},
        )

        with (
            patch.object(cost_estimation, "get_project_manager", lambda: mock_pm),
            patch.object(cost_estimation, "CostEstimationService") as mock_service,
        ):
            mock_service.return_value.compute = AsyncMock(return_value={})
            with TestClient(_make_app()) as client:
                response = client.get(
                    "/api/v1/projects/demo/cost-estimate",
                    params={
                        "reference_unit_id": "E1U1",
                        "narration_delivery": "use_tts",
                    },
                )

        assert response.status_code == 200, response.text
        call = mock_service.return_value.compute.await_args
        assert call is not None
        options = call.kwargs["reference_request_options"]["E1U1"]
        assert options.to_payload() == {"narration_delivery": "use_tts"}

    def test_delivery_without_unit_keeps_project_quote_scope(self):
        mock_pm = _mock_pm(project_exists=True, load_project={"episodes": []})

        with (
            patch.object(cost_estimation, "get_project_manager", lambda: mock_pm),
            patch.object(cost_estimation, "CostEstimationService") as mock_service,
        ):
            mock_service.return_value.compute = AsyncMock(return_value={})
            with TestClient(_make_app()) as client:
                response = client.get(
                    "/api/v1/projects/demo/cost-estimate",
                    params={"narration_delivery": "use_tts"},
                )

        assert response.status_code == 200
        assert mock_service.return_value.compute.await_args.kwargs["reference_request_options"] is None

    def test_unit_quote_rejects_unknown_unit(self):
        mock_pm = _mock_pm(
            project_exists=True,
            load_project={"episodes": [{"script_file": "ep1.json"}]},
            load_script={"video_units": []},
        )

        with patch.object(cost_estimation, "get_project_manager", lambda: mock_pm):
            with TestClient(_make_app()) as client:
                response = client.get(
                    "/api/v1/projects/demo/cost-estimate",
                    params={"reference_unit_id": "missing"},
                )

        assert response.status_code == 404

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
