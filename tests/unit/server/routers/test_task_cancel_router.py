"""
任务取消 API 端点测试：
  - GET  /tasks/{task_id}/cancel-preview
  - POST /tasks/{task_id}/cancel
  - GET  /projects/{project_name}/tasks/cancel-all-preview
  - POST /projects/{project_name}/tasks/cancel-all
"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.generation.generation_queue import GenerationQueue
from lib.i18n import MESSAGES
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import tasks as tasks_router
from tests.auth_deps import AUTH_DEPENDENCIES

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
        self._cancel_task_result.setdefault("cancelled", [])
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
    app.include_router(tasks_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
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
        assert body["skipped_terminal"] == []

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

    def test_skipped_terminal_failure_reason_renders_per_locale(self, monkeypatch):
        """取消命中已失败任务时，响应里的失败原因与列表/详情/SSE 同口径按请求语言渲染。"""
        stored = '[video_duration_not_supported] {"duration": 7, "supported": "5, 10"}'
        result = {
            "cancelled": [],
            "skipped_terminal": [{"task_id": "failed-task", "status": "failed", "error_message": stored}],
        }
        fake = _FakeQueue(cancel_task_result=result)
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: fake)

        app = _make_app()
        with TestClient(app) as client:
            resp = client.post("/api/v1/tasks/failed-task/cancel", headers={"Accept-Language": "en"})

        assert resp.status_code == 200
        expected = MESSAGES["en"]["video_duration_not_supported"].format(duration=7, supported="5, 10")
        assert resp.json()["skipped_terminal"][0]["error_message"] == expected


# ---------------------------------------------------------------------------
# Tests: running task is not cancellable (real queue)
# ---------------------------------------------------------------------------


class TestRunningTaskNotCancellable:
    @pytest.fixture
    async def running_task(self, db_factory, monkeypatch) -> tuple[GenerationQueue, str]:
        queue = GenerationQueue(session_factory=db_factory)
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="episode_01.json",
        )
        claimed = await queue.claim_next_task(media_type="video")
        assert claimed is not None
        assert claimed["task_id"] == enqueued["task_id"]
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: queue)
        return queue, enqueued["task_id"]

    @pytest.mark.parametrize("locale", ["zh", "en", "vi"])
    @pytest.mark.parametrize(
        ("method", "path"),
        [("POST", "/api/v1/tasks/{id}/cancel"), ("GET", "/api/v1/tasks/{id}/cancel-preview")],
    )
    async def test_running_task_returns_409_in_request_locale_and_keeps_running(
        self, running_task, locale, method, path
    ):
        queue, task_id = running_task
        transport = httpx.ASGITransport(app=_make_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.request(method, path.format(id=task_id), headers={"Accept-Language": locale})

        assert resp.status_code == 409
        assert resp.json()["detail"] == MESSAGES[locale]["task_running_not_cancellable"].format(id=task_id)
        row = await queue.get_task(task_id)
        assert row is not None
        assert row["status"] == "running"

    async def test_queued_dependent_of_running_task_is_left_queued(self, running_task):
        queue, task_id = running_task
        child = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S02",
            payload={},
            script_file="episode_01.json",
            dependency_task_id=task_id,
        )
        transport = httpx.ASGITransport(app=_make_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/tasks/{task_id}/cancel")

        assert resp.status_code == 409
        child_row = await queue.get_task(child["task_id"])
        assert child_row is not None
        assert child_row["status"] == "queued"


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
