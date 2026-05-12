from types import SimpleNamespace

import pytest

import lib.db
import server.app as app_module
from server.routers import assistant as assistant_router


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
    def test_create_generation_worker(self, monkeypatch):
        worker = _FakeWorker()
        monkeypatch.setattr(app_module, "GenerationWorker", lambda: worker)
        created = app_module.create_generation_worker()
        assert created is worker

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
