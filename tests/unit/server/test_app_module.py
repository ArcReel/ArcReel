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
@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
async def test_remote_mcp_paths_reach_host_without_redirect(path: str) -> None:
    """规范端点与兼容入口都直接落到远程 MCP 宿主；未进入 lifespan 时宿主固定回 503。"""
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://internal") as client:
        response = await client.post(path, follow_redirects=False)

    assert response.status_code == 503
    assert response.text == "MCP server is not running"


def _recording_app(content_type: str, body: bytes, events: list[str]):
    """先发响应头再发 body 的最小 ASGI 应用；发 body 前记录响应头是否已到达客户端。"""

    async def app(scope, receive, send):
        await send(
            {"type": "http.response.start", "status": 200, "headers": [(b"content-type", content_type.encode())]}
        )
        events.append("start-delivered" if "client-got-start" in events else "start-held")
        await send({"type": "http.response.body", "body": body, "more_body": False})

    return app


def _recording_send(events: list[str], messages: list[dict]):
    async def send(message):
        if message["type"] == "http.response.start":
            events.append("client-got-start")
        messages.append(message)

    return send


async def _call(app, accept: str, events: list[str], *, accept_encoding: str = "gzip") -> list[dict]:
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"accept", accept.encode()), (b"accept-encoding", accept_encoding.encode())],
    }
    await app_module.ResponseCompressionMiddleware(app)(scope, receive, _recording_send(events, messages))
    return messages


async def test_compression_gzips_large_json():
    events: list[str] = []
    messages = await _call(_recording_app("application/json", b'{"k": "v"}' * 200, events), "*/*", events)

    headers = dict(messages[0]["headers"])
    assert headers[b"content-encoding"] == b"gzip"


async def test_compression_skips_media():
    events: list[str] = []
    messages = await _call(_recording_app("image/png", b"\x89PNG" * 500, events), "*/*", events)

    assert b"content-encoding" not in dict(messages[0]["headers"])


@pytest.mark.parametrize("accept", ["text/event-stream", "*/*"])
@pytest.mark.parametrize("accept_encoding", ["gzip", ""])
async def test_event_stream_headers_reach_the_client_before_the_first_event(accept: str, accept_encoding: str):
    """事件流按响应类型识别，与请求是否声明 Accept 无关：响应头在首个事件前送达，body 原样透传。"""
    events: list[str] = []
    body = b"data: hello\n\n" * 100
    messages = await _call(
        _recording_app("text/event-stream", body, events), accept, events, accept_encoding=accept_encoding
    )

    assert events[:2] == ["client-got-start", "start-delivered"]
    assert b"content-encoding" not in dict(messages[0]["headers"])
    assert messages[1]["body"] == body


async def test_excluded_media_sent_by_pathsend_gets_a_single_response_start():
    """媒体响应头已即时放行时，pathsend 不再重复发送响应头。"""
    events: list[str] = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"video/mp4")]})
        await send({"type": "http.response.pathsend", "path": "/tmp/clip.mp4"})

    messages = await _call(app, "*/*", events)

    assert [message["type"] for message in messages] == ["http.response.start", "http.response.pathsend"]
