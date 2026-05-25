"""
任务取消 API 端点测试：
  - GET  /tasks/{task_id}/cancel-preview
  - POST /tasks/{task_id}/cancel
  - GET  /projects/{project_name}/tasks/cancel-all-preview
  - POST /projects/{project_name}/tasks/cancel-all
"""

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
        self._cancel_all_result = cancel_all_result or {"cancelled_count": 0, "skipped_running_count": 0}
        # ADR 0006: 单点 cancel 现返回 {cancelled, cancelling, skipped_terminal}
        self._cancel_task_result.setdefault("cancelled", [])
        self._cancel_task_result.setdefault("cancelling", [])
        self._cancel_task_result.setdefault("skipped_terminal", [])

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


def _make_app() -> FastAPI:
    """构建用于测试的最小 FastAPI 应用，注入假用户。"""
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(tasks_router.router, prefix="/api/v1")
    return app


# ---------------------------------------------------------------------------
# Tests: cancel-preview
# ---------------------------------------------------------------------------


class TestCancelPreview:
    def test_returns_preview_for_queued_task(self, monkeypatch):
        preview = {
            "task": {"task_id": "t1", "task_type": "image", "resource_id": "scene-1"},
            "cascaded": [],
        }
        fake = _FakeQueue(cancel_preview_result=preview)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/api/v1/tasks/t1/cancel-preview")

        assert resp.status_code == 200
        body = resp.json()
        assert body["task"]["task_id"] == "t1"
        assert body["cascaded"] == []

    def test_running_task_preview_returns_status(self, monkeypatch):
        """ADR 0006 放宽：preview 不再因 running 而拒绝，返回 task.status 让前端判断。"""
        preview = {
            "task": {"task_id": "t2", "task_type": "video", "resource_id": "scene-2", "status": "running"},
            "cascaded": [],
        }
        fake = _FakeQueue(cancel_preview_result=preview)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/api/v1/tasks/t2/cancel-preview")

        assert resp.status_code == 200
        assert resp.json()["task"]["status"] == "running"

    def test_returns_400_for_nonexistent_task(self, monkeypatch):
        fake = _FakeQueue(cancel_preview_error="任务 'missing' 不存在")
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app()
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
            "cancelling": [],
            "skipped_terminal": [],
        }
        fake = _FakeQueue(cancel_task_result=result)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app()
        with TestClient(app) as client:
            resp = client.post("/api/v1/tasks/t1/cancel")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["cancelled"]) == 1
        assert body["cancelled"][0]["task_id"] == "t1"
        assert body["cancelling"] == []
        assert body["skipped_terminal"] == []

    def test_cancels_running_task_returns_cancelling(self, monkeypatch):
        result = {
            "cancelled": [],
            "cancelling": ["running-task"],
            "skipped_terminal": [],
        }
        fake = _FakeQueue(cancel_task_result=result)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app()
        with TestClient(app) as client:
            resp = client.post("/api/v1/tasks/running-task/cancel")

        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelling"] == ["running-task"]
        assert body["cancelled"] == []

    def test_returns_400_for_nonexistent_task(self, monkeypatch):
        fake = _FakeQueue(cancel_task_error="任务 'ghost' 不存在")
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app()
        with TestClient(app) as client:
            resp = client.post("/api/v1/tasks/ghost/cancel")

        assert resp.status_code == 400
        assert "不存在" in resp.json()["detail"]

    def test_cancels_terminal_task_returns_skipped_terminal(self, monkeypatch):
        result = {
            "cancelled": [],
            "cancelling": [],
            "skipped_terminal": [{"task_id": "done-task", "status": "succeeded"}],
        }
        fake = _FakeQueue(cancel_task_result=result)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app()
        with TestClient(app) as client:
            resp = client.post("/api/v1/tasks/done-task/cancel")

        assert resp.status_code == 200
        body = resp.json()
        assert body["skipped_terminal"][0]["task_id"] == "done-task"


# ---------------------------------------------------------------------------
# Tests: cancel-all-preview
# ---------------------------------------------------------------------------


class TestCancelAllPreview:
    def test_returns_queued_count(self, monkeypatch):
        fake = _FakeQueue(cancel_all_preview_count=5)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/api/v1/projects/my-project/tasks/cancel-all-preview")

        assert resp.status_code == 200
        assert resp.json() == {"queued_count": 5}

    def test_returns_zero_when_no_queued_tasks(self, monkeypatch):
        fake = _FakeQueue(cancel_all_preview_count=0)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app()
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
            "skipped_running_count": 0,
        }
        fake = _FakeQueue(cancel_all_result=result)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app()
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/my-project/tasks/cancel-all")

        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled_count"] == 3
        assert body["skipped_running_count"] == 0

    def test_returns_zero_when_nothing_to_cancel(self, monkeypatch):
        result = {"cancelled_count": 0, "skipped_running_count": 0}
        fake = _FakeQueue(cancel_all_result=result)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app()
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/empty-project/tasks/cancel-all")

        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled_count"] == 0
        assert body["skipped_running_count"] == 0
