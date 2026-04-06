"""Tests for cost estimation router."""

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import cost_estimation


def _make_app():
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(cost_estimation.router, prefix="/api/v1")
    return app


class TestCostEstimationRouter:
    def test_project_not_found_returns_404(self):
        with patch.object(cost_estimation, "ProjectManager") as MockPM:
            MockPM.return_value.project_exists.return_value = False
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

        with (
            patch.object(cost_estimation, "ProjectManager") as MockPM,
            patch.object(cost_estimation, "CostEstimationService") as MockService,
        ):
            MockPM.return_value.project_exists.return_value = True
            MockPM.return_value.load_project.return_value = {"episodes": []}
            MockService.return_value.compute = AsyncMock(return_value=fake_result)

            with TestClient(_make_app()) as client:
                resp = client.get("/api/v1/projects/demo/cost-estimate")

        assert resp.status_code == 200
        body = resp.json()
        assert body["project_name"] == "demo"
        assert "models" in body
        assert "episodes" in body
        assert "project_totals" in body

    def test_no_auth_returns_401(self):
        app = FastAPI()
        # Do NOT override the auth dependency — real auth should reject
        app.include_router(cost_estimation.router, prefix="/api/v1")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/v1/projects/demo/cost-estimate")
        assert resp.status_code == 401
