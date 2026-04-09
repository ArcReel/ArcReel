"""
任务取消 API 端点测试：
  - GET  /tasks/{task_id}/cancel-preview
  - POST /tasks/{task_id}/cancel
  - GET  /projects/{project_name}/tasks/cancel-all-preview
  - POST /projects/{project_name}/tasks/cancel-all
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import tasks as tasks_router


# ---------------------------------------------------------------------------
# Fake queue helpers
# ---------------------------------------------------------------------------


class _FakeQueue:
    """仅实现取消相关方法的最小 Fake。"""

    def __init__(
        self,
        *,
        cancel_preview_result=None,
        cancel_preview_error: str | None = None,
        cancel_task_result=None,
        cancel_task_error: str | None = None,
        cancel_all_preview_count: int = 0,
        cancel_all_result=None,
    ):
        self._cancel_preview_result = cancel_preview_result or {}
        self._cancel_preview_error = cancel_preview_error
        self._cancel_task_result = cancel_task_result or {}
        self._cancel_task_error = cancel_task_error
        self._cancel_all_preview_count = cancel_all_preview_count
        self._cancel_all_result = cancel_all_result or {"cancelled_count": 0, "tasks": []}

    async def get_cancel_preview(self, task_id: str):
        if self._cancel_preview_error:
            raise ValueError(self._cancel_preview_error)
        return self._cancel_preview_result

    async def cancel_task(self, task_id: str):
        if self._cancel_task_error:
            raise ValueError(self._cancel_task_error)
        return self._cancel_task_result

    async def get_cancel_all_preview(self, project_name: str) -> int:
        return self._cancel_all_preview_count

    async def cancel_all_queued(self, project_name: str):
        return self._cancel_all_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_app(fake_queue: _FakeQueue) -> FastAPI:
    """构建用于测试的最小 FastAPI 应用，注入假队列和假用户。"""
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(
        id="default", sub="testuser", role="admin"
    )
    app.include_router(tasks_router.router, prefix="/api/v1")
    return app


# ---------------------------------------------------------------------------
# Tests: cancel-preview
# ---------------------------------------------------------------------------


class TestCancelPreview:
    def test_returns_preview_for_queued_task(self, monkeypatch):
        preview = {
            "task_id": "t1",
            "status": "queued",
            "can_cancel": True,
            "dependents": [],
        }
        fake = _FakeQueue(cancel_preview_result=preview)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app(fake)
        with TestClient(app) as client:
            resp = client.get("/api/v1/tasks/t1/cancel-preview")

        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == "t1"
        assert body["can_cancel"] is True

    def test_returns_400_for_running_task(self, monkeypatch):
        fake = _FakeQueue(cancel_preview_error="只有排队中的任务可以取消")
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app(fake)
        with TestClient(app) as client:
            resp = client.get("/api/v1/tasks/t2/cancel-preview")

        assert resp.status_code == 400
        assert "只有排队中的任务可以取消" in resp.json()["detail"]

    def test_returns_400_for_nonexistent_task(self, monkeypatch):
        fake = _FakeQueue(cancel_preview_error="任务 'missing' 不存在")
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app(fake)
        with TestClient(app) as client:
            resp = client.get("/api/v1/tasks/missing/cancel-preview")

        assert resp.status_code == 400
        assert "不存在" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Tests: cancel
# ---------------------------------------------------------------------------


class TestCancelTask:
    def test_cancels_queued_task(self, monkeypatch):
        result = {
            "cancelled": [{"task_id": "t1", "status": "cancelled"}],
            "skipped_running": [],
        }
        fake = _FakeQueue(cancel_task_result=result)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app(fake)
        with TestClient(app) as client:
            resp = client.post("/api/v1/tasks/t1/cancel")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["cancelled"]) == 1
        assert body["cancelled"][0]["task_id"] == "t1"

    def test_returns_400_for_nonexistent_task(self, monkeypatch):
        fake = _FakeQueue(cancel_task_error="任务 'ghost' 不存在")
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app(fake)
        with TestClient(app) as client:
            resp = client.post("/api/v1/tasks/ghost/cancel")

        assert resp.status_code == 400
        assert "不存在" in resp.json()["detail"]

    def test_returns_400_for_running_task(self, monkeypatch):
        fake = _FakeQueue(cancel_task_error="只有排队中的任务可以取消")
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app(fake)
        with TestClient(app) as client:
            resp = client.post("/api/v1/tasks/running-task/cancel")

        assert resp.status_code == 400
        assert "只有排队中的任务可以取消" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Tests: cancel-all-preview
# ---------------------------------------------------------------------------


class TestCancelAllPreview:
    def test_returns_queued_count(self, monkeypatch):
        fake = _FakeQueue(cancel_all_preview_count=5)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app(fake)
        with TestClient(app) as client:
            resp = client.get("/api/v1/projects/my-project/tasks/cancel-all-preview")

        assert resp.status_code == 200
        assert resp.json() == {"queued_count": 5}

    def test_returns_zero_when_no_queued_tasks(self, monkeypatch):
        fake = _FakeQueue(cancel_all_preview_count=0)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app(fake)
        with TestClient(app) as client:
            resp = client.get("/api/v1/projects/empty-project/tasks/cancel-all-preview")

        assert resp.status_code == 200
        assert resp.json() == {"queued_count": 0}


# ---------------------------------------------------------------------------
# Tests: cancel-all
# ---------------------------------------------------------------------------


class TestCancelAllQueued:
    def test_cancels_all_queued_tasks(self, monkeypatch):
        result = {
            "cancelled_count": 3,
            "tasks": [
                {"task_id": "t1", "status": "cancelled"},
                {"task_id": "t2", "status": "cancelled"},
                {"task_id": "t3", "status": "cancelled"},
            ],
        }
        fake = _FakeQueue(cancel_all_result=result)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app(fake)
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/my-project/tasks/cancel-all")

        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled_count"] == 3
        assert len(body["tasks"]) == 3

    def test_returns_zero_when_nothing_to_cancel(self, monkeypatch):
        result = {"cancelled_count": 0, "tasks": []}
        fake = _FakeQueue(cancel_all_result=result)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app(fake)
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/empty-project/tasks/cancel-all")

        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled_count"] == 0
        assert body["tasks"] == []
