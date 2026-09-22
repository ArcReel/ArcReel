from types import SimpleNamespace

import httpx
import pytest

import lib.db
import server.app as app_module
from server.routers import assistant as assistant_router
from server.services.tasks.generation_tasks import execute_generation_task
from server.services.tasks.resume_executor import execute_resume_video_task


async def _noop_async(*args, **kwargs):
    """No-op coroutine for mocking async functions in tests."""


class _FakeWorker:
    def __init__(self):
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


class TestAppModule:
    def test_create_generation_worker_injects_server_executors(self, monkeypatch):
        worker = _FakeWorker()
        received: dict[str, object] = {}

        def _build(**kwargs):
            received.update(kwargs)
            return worker

        monkeypatch.setattr(app_module, "GenerationWorker", _build)
        created = app_module.create_generation_worker()
        assert created is worker
        assert received == {"executor": execute_generation_task, "resume_executor": execute_resume_video_task}

    @pytest.mark.asyncio
    async def test_lifespan_starts_and_stops_worker(self, monkeypatch):
        worker = _FakeWorker()
        monkeypatch.setattr(app_module, "create_generation_worker", lambda: worker)
        monkeypatch.setattr(app_module, "ensure_auth_password", lambda: "test")
        monkeypatch.setattr(app_module, "init_db", _noop_async)
        monkeypatch.setattr(lib.db, "init_db", _noop_async)
        monkeypatch.setattr(assistant_router.assistant_service, "startup", _noop_async)
        monkeypatch.setattr(assistant_router.assistant_service, "shutdown", _noop_async)

        app = app_module.app
        app.state = SimpleNamespace()

        async with app_module.lifespan(app):
            assert worker.started
            assert hasattr(app.state, "generation_worker")

        assert worker.stopped

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("auth_enabled", "expect_warning"), [("false", True), ("true", False)])
    async def test_lifespan_warns_when_auth_disabled(self, monkeypatch, caplog, auth_enabled, expect_warning):
        monkeypatch.setenv("AUTH_ENABLED", auth_enabled)
        monkeypatch.setattr(app_module, "create_generation_worker", lambda: _FakeWorker())
        monkeypatch.setattr(app_module, "ensure_auth_password", lambda: "test")
        monkeypatch.setattr(app_module, "init_db", _noop_async)
        monkeypatch.setattr(lib.db, "init_db", _noop_async)
        monkeypatch.setattr(assistant_router.assistant_service, "startup", _noop_async)
        monkeypatch.setattr(assistant_router.assistant_service, "shutdown", _noop_async)

        app = app_module.app
        app.state = SimpleNamespace()

        with caplog.at_level("WARNING", logger="server.auth"):
            async with app_module.lifespan(app):
                pass

        warnings = [
            r
            for r in caplog.records
            if r.name == "server.auth" and r.levelname == "WARNING" and "AUTH_ENABLED" in r.getMessage()
        ]
        assert bool(warnings) is expect_warning


class TestListenEnvVars:
    """``LISTEN_HOST`` / ``LISTEN_PORT`` 的解析仅在 ``__main__`` 块被 uvicorn 消费，
    导入 ``server.app`` 不会触发；通过共用的模块级 ``_resolve_listen_addr()`` 函数
    测试同一份生产解析逻辑，避免测试 / 生产代码漂移。"""

    def test_defaults_match_existing_behavior(self, monkeypatch):
        monkeypatch.delenv("LISTEN_HOST", raising=False)
        monkeypatch.delenv("LISTEN_PORT", raising=False)
        assert app_module._resolve_listen_addr() == ("0.0.0.0", 1241)

    def test_env_overrides_take_effect(self, monkeypatch):
        monkeypatch.setenv("LISTEN_HOST", "127.0.0.1")
        monkeypatch.setenv("LISTEN_PORT", "18080")
        assert app_module._resolve_listen_addr() == ("127.0.0.1", 18080)

    def test_empty_listen_port_falls_back_to_default(self, monkeypatch):
        """`.env` 误写 `LISTEN_PORT=`（空值）不应让 `int("")` 抛 ValueError。"""
        monkeypatch.setenv("LISTEN_HOST", "")
        monkeypatch.setenv("LISTEN_PORT", "")
        assert app_module._resolve_listen_addr() == ("0.0.0.0", 1241)


@pytest.mark.asyncio
async def test_mcp_mount_redirect_stays_relative_behind_https_proxy() -> None:
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://internal") as client:
        response = await client.post(
            "/mcp",
            headers={"Host": "arcreel.example.com", "X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert response.headers["location"] == "/mcp/"
