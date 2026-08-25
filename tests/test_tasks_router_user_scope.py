"""Task HTTP APIs must stay inside the authenticated user's namespace."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import tasks as tasks_router
from tests.auth_deps import AUTH_DEPENDENCIES

pytestmark = pytest.mark.unit


class _ScopedQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_task_stats(self, **kwargs):
        self.calls.append(("stats", kwargs))
        return {"total": 0}

    async def list_tasks(self, **kwargs):
        self.calls.append(("list", kwargs))
        return {"items": [], "total": 0, "page": 1, "page_size": 50}

    async def get_task(self, task_id: str, **kwargs):
        self.calls.append(("get", {"task_id": task_id, **kwargs}))
        return {"task_id": task_id, "user_id": "center-user"}

    async def get_cancel_preview(self, task_id: str, **kwargs):
        self.calls.append(("cancel-preview", {"task_id": task_id, **kwargs}))
        return {"task": {"task_id": task_id}, "cascaded": []}

    async def cancel_task(self, task_id: str, **kwargs):
        self.calls.append(("cancel", {"task_id": task_id, **kwargs}))
        return {"cancelled": [], "cancelling": [], "skipped_terminal": []}

    async def get_cancel_all_preview(self, project_name: str, **kwargs):
        self.calls.append(("cancel-all-preview", {"project_name": project_name, **kwargs}))
        return 0

    async def cancel_all_queued(self, project_name: str, **kwargs):
        self.calls.append(("cancel-all", {"project_name": project_name, **kwargs}))
        return {"cancelled_count": 0, "skipped_running_count": 0}


def _client(monkeypatch: pytest.MonkeyPatch, queue: _ScopedQueue) -> TestClient:
    monkeypatch.setattr(tasks_router, "get_task_queue", lambda: queue)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(
        id="center-user",
        sub="alice",
        role="user",
    )
    app.include_router(tasks_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


def test_every_task_http_operation_forwards_authenticated_user(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = _ScopedQueue()
    client = _client(monkeypatch, queue)

    assert client.get("/api/v1/tasks/stats").status_code == 200
    assert client.get("/api/v1/tasks").status_code == 200
    assert client.get("/api/v1/projects/demo/tasks").status_code == 200
    assert client.get("/api/v1/tasks/task-1").status_code == 200
    assert client.get("/api/v1/tasks/task-1/cancel-preview").status_code == 200
    assert client.post("/api/v1/tasks/task-1/cancel").status_code == 200
    assert client.get("/api/v1/projects/demo/tasks/cancel-all-preview").status_code == 200
    assert client.post("/api/v1/projects/demo/tasks/cancel-all").status_code == 200

    assert queue.calls
    assert all(kwargs["user_id"] == "center-user" for _operation, kwargs in queue.calls)
