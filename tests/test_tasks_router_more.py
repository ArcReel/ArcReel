from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user, get_current_user_flexible
from server.error_handlers import register_error_handlers
from server.routers import tasks as tasks_router


class _FakeQueue:
    def __init__(self, *, task=None):
        self.task = task

    async def get_task(self, task_id):
        return self.task


class TestTasksRouterMore:
    def test_get_task_not_found(self, monkeypatch):
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: _FakeQueue(task=None))
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.dependency_overrides[get_current_user_flexible] = lambda: CurrentUserInfo(
            id="default", sub="testuser", role="admin"
        )
        app.include_router(tasks_router.router, prefix="/api/v1")
        register_error_handlers(app)

        with TestClient(app) as client:
            resp = client.get("/api/v1/tasks/missing-task")
            assert resp.status_code == 404
            assert "不存在" in resp.json()["detail"]


class _RenderQueue:
    """Queue stub serving fresh task copies per call so in-place rendering does not leak."""

    def __init__(self, *, items=None, task=None):
        self._items = items if items is not None else []
        self._task = task

    async def list_tasks(self, **kwargs):
        return {
            "items": [dict(item) for item in self._items],
            "total": len(self._items),
            "page": 1,
            "page_size": 50,
        }

    async def get_task(self, task_id):
        return dict(self._task) if self._task is not None else None


class TestTaskErrorLocalization:
    def _client(self, monkeypatch, queue):
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: queue)
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.dependency_overrides[get_current_user_flexible] = lambda: CurrentUserInfo(
            id="default", sub="testuser", role="admin"
        )
        app.include_router(tasks_router.router, prefix="/api/v1")
        return TestClient(app)

    def test_list_tasks_renders_known_code_per_locale(self, monkeypatch):
        from lib.task_failure import encode_failure

        encoded = encode_failure("provider_unsupported_media", provider_id="grok", media_type="image")
        items = [{"task_id": "t1", "status": "failed", "error_message": encoded}]
        client = self._client(monkeypatch, _RenderQueue(items=items))

        en = client.get("/api/v1/tasks", headers={"Accept-Language": "en"}).json()["items"][0]
        assert en["error_message"] == "Provider grok does not support image generation"

        zh = client.get("/api/v1/tasks", headers={"Accept-Language": "zh"}).json()["items"][0]
        assert zh["error_message"] == "供应商 grok 不支持 image 生成"

        vi = client.get("/api/v1/tasks", headers={"Accept-Language": "vi"}).json()["items"][0]
        assert "grok" in vi["error_message"] and "image" in vi["error_message"]
        assert vi["error_message"] != en["error_message"]

    def test_list_tasks_defaults_to_zh_without_header(self, monkeypatch):
        from lib.task_failure import encode_failure

        items = [{"task_id": "t1", "error_message": encode_failure("restart_lost_image")}]
        client = self._client(monkeypatch, _RenderQueue(items=items))
        body = client.get("/api/v1/tasks").json()["items"][0]
        assert body["error_message"].startswith("图片任务")

    def test_list_tasks_passthrough_raw_and_legacy(self, monkeypatch):
        items = [
            {"task_id": "raw", "error_message": "RuntimeError: provider 500"},
            {"task_id": "legacy", "error_message": "[restart_lost] image 任务无法接续，需手动重试以避免重复计费"},
            {"task_id": "ok", "error_message": None},
        ]
        client = self._client(monkeypatch, _RenderQueue(items=items))
        out = client.get("/api/v1/tasks", headers={"Accept-Language": "en"}).json()["items"]
        by_id = {t["task_id"]: t["error_message"] for t in out}
        assert by_id["raw"] == "RuntimeError: provider 500"
        assert by_id["legacy"] == "[restart_lost] image 任务无法接续，需手动重试以避免重复计费"
        assert by_id["ok"] is None

    def test_get_task_renders_error_message(self, monkeypatch):
        from lib.task_failure import encode_failure

        task = {
            "task_id": "t9",
            "status": "failed",
            "error_message": encode_failure("resume_unsupported_provider", provider_id="vidu"),
        }
        client = self._client(monkeypatch, _RenderQueue(task=task))
        body = client.get("/api/v1/tasks/t9", headers={"Accept-Language": "en"}).json()["task"]
        assert body["error_message"] == (
            "Provider vidu does not support task resumption; please retry manually to avoid duplicate billing"
        )

    def test_project_tasks_renders_error_message(self, monkeypatch):
        from lib.task_failure import encode_failure

        items = [{"task_id": "p1", "error_message": encode_failure("restart_lost_audio")}]
        client = self._client(monkeypatch, _RenderQueue(items=items))
        body = client.get("/api/v1/projects/demo/tasks", headers={"Accept-Language": "en"}).json()["items"][0]
        assert body["error_message"].startswith("The audio task was interrupted")
