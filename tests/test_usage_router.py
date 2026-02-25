from fastapi import FastAPI
from fastapi.testclient import TestClient

from webui.server.routers import usage


class _FakeTracker:
    def get_stats(self, **kwargs):
        return {"total_cost": 1.2, "image_count": 1, "video_count": 2, "failed_count": 0, "total_count": 3}

    def get_calls(self, **kwargs):
        return {"items": [{"id": 1}], "total": 1, "page": kwargs["page"], "page_size": kwargs["page_size"]}

    def get_projects_list(self):
        return ["demo", "demo2"]


def _client(monkeypatch):
    monkeypatch.setattr(usage, "get_usage_tracker", lambda: _FakeTracker())
    app = FastAPI()
    app.include_router(usage.router, prefix="/api/v1")
    return TestClient(app)


class TestUsageRouter:
    def test_usage_endpoints(self, monkeypatch):
        with _client(monkeypatch) as client:
            stats = client.get("/api/v1/usage/stats?project_name=demo&start_date=2026-02-01&end_date=2026-02-10")
            assert stats.status_code == 200
            assert stats.json()["total_count"] == 3

            calls = client.get("/api/v1/usage/calls?page=2&page_size=10")
            assert calls.status_code == 200
            assert calls.json()["page"] == 2
            assert calls.json()["page_size"] == 10

            projects = client.get("/api/v1/usage/projects")
            assert projects.status_code == 200
            assert projects.json()["projects"] == ["demo", "demo2"]
