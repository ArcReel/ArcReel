from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from lib.i18n.display_names import DisplayNames
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.i18n import get_display_names, get_locale
from server.routers import tasks as tasks_router
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.factories import make_display_names


class _FakeQueue:
    def __init__(self, *, task=None):
        self.task = task

    async def get_task(self, task_id):
        return self.task


class TestTasksRouter:
    def test_get_task_not_found(self, monkeypatch):
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: _FakeQueue(task=None))
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(tasks_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)

        with TestClient(app) as client:
            resp = client.get("/api/v1/tasks/missing-task")
            assert resp.status_code == 404
            assert "不存在" in resp.json()["detail"]


class _RetryQueue:
    def __init__(self):
        self.calls: list[str] = []

    async def retry_artifact_download(self, task_id: str):
        self.calls.append(task_id)
        return {"task_id": task_id, "status": "running", "error_message": None}


class _RetryWorker:
    def __init__(self, *, timeout_error: Exception | None = None):
        self.tasks = []
        self.poll_timeouts = []
        self._timeout_error = timeout_error

    async def read_video_poll_timeout_seconds(self) -> int:
        if self._timeout_error is not None:
            raise self._timeout_error
        return 3600

    async def retry_artifact_download(self, task, *, poll_timeout_seconds: int):
        self.tasks.append(task)
        self.poll_timeouts.append(poll_timeout_seconds)


class TestRetryArtifactDownload:
    @staticmethod
    def _app(queue: _RetryQueue, worker: _RetryWorker | None) -> FastAPI:
        app = FastAPI()
        app.state.generation_worker = worker
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(tasks_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        return app

    def test_dispatches_existing_task_without_creating_another(self, monkeypatch):
        queue = _RetryQueue()
        worker = _RetryWorker()
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: queue)

        with TestClient(self._app(queue, worker)) as client:
            response = client.post("/api/v1/tasks/task-1/retry-download")

        assert response.status_code == 200
        assert queue.calls == ["task-1"]
        assert worker.tasks == [{"task_id": "task-1", "status": "running", "error_message": None}]
        assert worker.poll_timeouts == [3600]

    def test_unresolvable_poll_timeout_leaves_task_retryable(self, monkeypatch):
        # 轮询超时读的是配置库。它在翻状态之后失败就没有回滚点，任务会永久停在 running：
        # 既不被队列认领，也不再满足 retry-download 的资格条件。故必须先读再翻。
        queue = _RetryQueue()
        worker = _RetryWorker(timeout_error=RuntimeError("config database unavailable"))
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: queue)

        with TestClient(self._app(queue, worker), raise_server_exceptions=False) as client:
            response = client.post("/api/v1/tasks/task-1/retry-download")

        assert response.status_code == 500
        assert queue.calls == []
        assert worker.tasks == []

    def test_unavailable_worker_does_not_transition_task(self, monkeypatch):
        queue = _RetryQueue()
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: queue)

        with TestClient(self._app(queue, None)) as client:
            response = client.post("/api/v1/tasks/task-1/retry-download")

        assert response.status_code == 400
        assert queue.calls == []


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


def _display_names(request: Request) -> DisplayNames:
    """两个供应商使用同一模型 ID，显示名按供应商区分。"""
    return make_display_names(
        get_locale(request),
        providers={"custom-2": "我的中转站", "custom-3": "另一家中转站"},
        models={("custom-2", "I2V_H3"): "图生视频 H3", ("custom-3", "I2V_H3"): "另一家视频模型"},
    )


class TestTaskErrorLocalization:
    def _client(self, monkeypatch, queue):
        monkeypatch.setattr(tasks_router, "get_task_queue", lambda: queue)
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.dependency_overrides[get_display_names] = _display_names
        app.include_router(tasks_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        return TestClient(app)

    def test_video_warnings_use_the_selected_providers_model_name(self, monkeypatch):
        from lib.script.reference_video.duration_slots import resolve_duration_slot
        from lib.script.reference_video.script_preview import build_script_preview
        from lib.script.reference_video.voice_settings import VoiceRenderSettings
        from tests.factories import make_video_request_facts

        facts = make_video_request_facts(provider_id="custom-2", model_id="I2V_H3", voice_consistency="none")
        preview = build_script_preview(
            "@[角色]{你好}", {"characters": {"角色": {}}}, VoiceRenderSettings.from_request_facts(facts)
        )
        warnings = [
            resolve_duration_slot(7, [5, 10]).warning(provider=facts.provider_id, model=facts.model_id),
            *preview.warnings,
        ]
        client = self._client(monkeypatch, _RenderQueue(items=[{"task_id": "t1", "result": {"warnings": warnings}}]))

        for locale in ("zh", "en", "vi"):
            rendered = client.get("/api/v1/tasks", headers={"Accept-Language": locale}).json()["items"][0]["result"][
                "warnings"
            ]
            assert len(rendered) == 2
            for message in rendered:
                assert "图生视频 H3" in message
                assert "I2V_H3" not in message
                assert "另一家视频模型" not in message

    def test_list_tasks_renders_known_code_per_locale(self, monkeypatch):
        from lib.generation.task_failure import encode_failure

        encoded = encode_failure("provider_unsupported_media", provider_id="grok", media_type="image")
        items = [{"task_id": "t1", "status": "failed", "error_message": encoded}]
        client = self._client(monkeypatch, _RenderQueue(items=items))

        en = client.get("/api/v1/tasks", headers={"Accept-Language": "en"}).json()["items"][0]
        assert en["error_message"] == "Provider Grok does not support image generation"

        zh = client.get("/api/v1/tasks", headers={"Accept-Language": "zh"}).json()["items"][0]
        assert zh["error_message"] == "供应商 Grok 不支持 image 生成"

        vi = client.get("/api/v1/tasks", headers={"Accept-Language": "vi"}).json()["items"][0]
        assert "Grok" in vi["error_message"]
        assert "image" in vi["error_message"]
        assert vi["error_message"] != en["error_message"]

    def test_list_tasks_defaults_to_zh_without_header(self, monkeypatch):
        from lib.generation.task_failure import encode_failure

        items = [{"task_id": "t1", "error_message": encode_failure("restart_lost_image")}]
        client = self._client(monkeypatch, _RenderQueue(items=items))
        body = client.get("/api/v1/tasks").json()["items"][0]
        assert body["error_message"].startswith("图片任务")

    def test_render_problems_are_localized_without_changing_the_stored_envelope(self, monkeypatch):
        from lib.generation.generation_result import encode_generation_problem
        from lib.i18n import _
        from lib.jianying_draft.errors import JianyingDraftError
        from server.services.tasks.render_tasks import render_problem

        cases = [
            ("jianying_draft_presentation_unavailable", {"unit_id": "E1U2"}),
            ("jianying_draft_hold_frame_unavailable", {"clip_id": "c1"}),
            ("jianying_draft_acceptance_failed", {}),
            ("jianying_draft_blocked", {"issues": [{"unit_id": "E1U2", "code": "video_missing"}]}),
        ]
        items = [
            {
                "task_id": code,
                "error_message": encode_generation_problem(
                    render_problem(JianyingDraftError(code, "诊断原文", **params))
                ),
            }
            for code, params in cases
        ]
        original = [dict(item) for item in items]
        client = self._client(monkeypatch, _RenderQueue(items=items))
        for locale in ("zh", "en", "vi"):
            rows = client.get("/api/v1/tasks", headers={"Accept-Language": locale}).json()["items"]
            for row, (code, params) in zip(rows, cases, strict=True):
                translated_params = {**params, "units": "E1U2"} if "issues" in params else params
                assert row["error_message"] == _(code, locale=locale, **translated_params)
                assert row["error_message"] not in {code, "诊断原文"}
                assert (row["error_code"], row["error_params"]) == (code, params)
        assert items == original

    def test_edit_timeline_problems_in_render_tasks_use_the_same_keys_as_http(self, monkeypatch):
        from lib.edit_timeline.errors import EditTimelineError
        from lib.generation.generation_result import encode_generation_problem
        from lib.i18n import _
        from server.services.tasks.render_tasks import render_problem

        cases = [
            ("timeline_invalid", {"file": "tl-1.json"}, "edit_timeline_invalid", {"file": "tl-1.json"}),
            ("project_not_found", {"project": "demo"}, "project_not_found", {"name": "demo"}),
        ]
        items = [
            {
                "task_id": code,
                "error_message": encode_generation_problem(
                    render_problem(EditTimelineError(code, "诊断原文", **params))
                ),
            }
            for code, params, _key, _values in cases
        ]
        client = self._client(monkeypatch, _RenderQueue(items=items))
        for locale in ("zh", "en", "vi"):
            rows = client.get("/api/v1/tasks", headers={"Accept-Language": locale}).json()["items"]
            for row, (code, params, key, values) in zip(rows, cases, strict=True):
                assert row["error_message"] == _(key, locale=locale, **values)
                assert "{" not in row["error_message"]
                assert (row["error_code"], row["error_params"]) == (code, params)

    def test_episode_planning_failure_is_localized_without_the_diagnostic_detail(self, monkeypatch):
        from lib.generation.generation_result import GenerationAction, GenerationProblem, encode_generation_problem

        problem = GenerationProblem(
            code="episode_planning_failed", detail="源文件不存在：source/a.txt", action=GenerationAction.RETRY
        )
        items = [{"task_id": "plan", "error_message": encode_generation_problem(problem)}]
        client = self._client(monkeypatch, _RenderQueue(items=items))
        rendered = {
            locale: client.get("/api/v1/tasks", headers={"Accept-Language": locale}).json()["items"][0]["error_message"]
            for locale in ("zh", "en", "vi")
        }
        assert rendered == {
            "zh": "出现错误，请重试；反复失败时，可以交给 Agent 排查",
            "en": "An error occurred. Try again, or hand it to the Agent if it keeps failing",
            "vi": "Đã xảy ra lỗi. Hãy thử lại, hoặc giao cho Agent nếu vẫn thất bại",
        }

    def test_text_task_video_model_failure_is_localized_with_the_server_text_as_detail(self, monkeypatch):
        from lib.generation.generation_result import GenerationAction, GenerationProblem, encode_generation_problem
        from lib.i18n import _

        params = {"provider": "custom-2", "model": "I2V_H3"}
        problem = GenerationProblem(
            code="video_capability_missing_r2v",
            detail="❌ 服务端原文",
            action=GenerationAction.CONFIGURE_PROVIDER,
            params=params,
        )
        items = [{"task_id": "plan", "status": "failed", "error_message": encode_generation_problem(problem)}]
        client = self._client(monkeypatch, _RenderQueue(items=items))
        for locale in ("zh", "en", "vi"):
            row = client.get("/api/v1/tasks", headers={"Accept-Language": locale}).json()["items"][0]
            assert row["error_message"] == _(
                "video_capability_missing_r2v", locale=locale, provider="我的中转站", model="图生视频 H3"
            )
            assert (row["error_code"], row["error_params"], row["error_detail"]) == (
                "video_capability_missing_r2v",
                params,
                "❌ 服务端原文",
            )

    def test_failure_of_a_deleted_provider_names_it_generically(self, monkeypatch):
        from lib.generation.task_failure import encode_failure
        from lib.i18n import _

        encoded = encode_failure("video_capability_missing_r2v", provider="custom-9", model="I2V_H3")
        items = [{"task_id": "gen", "status": "failed", "error_message": encoded}]
        client = self._client(monkeypatch, _RenderQueue(items=items))
        for locale in ("zh", "en", "vi"):
            row = client.get("/api/v1/tasks", headers={"Accept-Language": locale}).json()["items"][0]
            assert _("deleted_provider_display_name", locale=locale) in row["error_message"]
            assert "custom-9" not in row["error_message"]
            assert row["error_params"] == {"provider": "custom-9", "model": "I2V_H3"}
            assert row["error_detail"] == encoded

    def test_list_tasks_passthrough_raw_and_legacy(self, monkeypatch):
        from lib.generation.generation_result import GenerationAction, GenerationProblem, encode_generation_problem

        refused = GenerationProblem(
            code="generation_refused", detail="❌ 源文读取失败", action=GenerationAction.FIX_INPUT
        )
        items = [
            {"task_id": "raw", "error_message": "RuntimeError: provider 500"},
            {"task_id": "legacy", "error_message": "[restart_lost] image 任务无法接续，需手动重试以避免重复计费"},
            {"task_id": "refused", "error_message": encode_generation_problem(refused)},
            {"task_id": "ok", "error_message": None},
        ]
        client = self._client(monkeypatch, _RenderQueue(items=items))
        out = client.get("/api/v1/tasks", headers={"Accept-Language": "en"}).json()["items"]
        by_id = {t["task_id"]: t["error_message"] for t in out}
        assert by_id["raw"] == "RuntimeError: provider 500"
        assert by_id["legacy"] == "[restart_lost] image 任务无法接续，需手动重试以避免重复计费"
        assert by_id["refused"] == "❌ 源文读取失败"
        assert by_id["ok"] is None
        assert all("error_detail" not in t for t in out)

    def test_get_task_renders_error_message(self, monkeypatch):
        from lib.generation.task_failure import encode_failure

        task = {
            "task_id": "t9",
            "status": "failed",
            "error_message": encode_failure("resume_unsupported_provider", provider_id="vidu"),
        }
        client = self._client(monkeypatch, _RenderQueue(task=task))
        body = client.get("/api/v1/tasks/t9", headers={"Accept-Language": "en"}).json()["task"]
        assert body["error_message"] == (
            "Provider Vidu does not support task resumption; please retry manually to avoid duplicate billing"
        )

    def test_project_tasks_renders_error_message(self, monkeypatch):
        from lib.generation.task_failure import encode_failure

        items = [{"task_id": "p1", "error_message": encode_failure("restart_lost_audio")}]
        client = self._client(monkeypatch, _RenderQueue(items=items))
        body = client.get("/api/v1/projects/demo/tasks", headers={"Accept-Language": "en"}).json()["items"][0]
        assert body["error_message"].startswith("The audio task was interrupted")


class TestTaskResourceRefs:
    """任务的条目 ID 带集 ID：列表附上所属集的标题、播出位置与集内 ID，界面据此指称条目。"""

    def test_list_and_cancel_preview_carry_the_episode_title_and_position(self, monkeypatch, tmp_path):
        projects = ProjectManager(tmp_path / "projects")
        projects.create_project("demo")
        projects.create_project_metadata("demo", "Demo")
        projects.update_project(
            "demo",
            lambda project: project.update(
                episodes=[
                    {"episode": 7, "title": "山门", "script_file": "scripts/episode_7.json"},
                    {"episode": 3, "title": "下山", "script_file": "scripts/episode_3.json"},
                ]
            ),
        )
        items = [
            {"task_id": "a", "project_name": "demo", "resource_id": "E3S02"},
            {"task_id": "b", "project_name": "demo", "resource_id": "张三"},
            {"task_id": "c", "project_name": "missing", "resource_id": "E3S02"},
        ]

        class _Queue(_RenderQueue):
            async def get_cancel_preview(self, task_id):
                return {"task": dict(items[0]), "cascaded": [{**items[0], "task_id": "d", "resource_id": "E7U01"}]}

        monkeypatch.setattr(tasks_router, "get_project_manager", lambda: projects)
        client = TestTaskErrorLocalization()._client(monkeypatch, _Queue(items=items))

        listed = client.get("/api/v1/tasks").json()["items"]
        preview = client.get("/api/v1/tasks/a/cancel-preview").json()

        assert {task["task_id"]: task["resource_ref"] for task in listed} == {
            "a": {"episode_title": "下山", "episode_position": 2, "item_id": "S02"},
            "b": None,
            "c": None,
        }
        assert preview["task"]["resource_ref"] == {"episode_title": "下山", "episode_position": 2, "item_id": "S02"}
        assert [task["resource_ref"] for task in preview["cascaded"]] == [
            {"episode_title": "山门", "episode_position": 1, "item_id": "U01"}
        ]
