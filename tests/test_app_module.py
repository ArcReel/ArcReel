from pathlib import Path
from types import SimpleNamespace

import pytest

import webui.server.app as app_module


class _FakeWorker:
    def __init__(self):
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


class TestAppModule:
    def test_serve_frontend_index_returns_503_when_missing(self, monkeypatch):
        monkeypatch.setattr(app_module, "frontend_index_file", Path("/tmp/__not_exists__/index.html"))
        resp = app_module._serve_frontend_index()
        assert resp.status_code == 503

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

        app = app_module.app
        app.state = SimpleNamespace()

        async with app_module.lifespan(app):
            assert worker.started
            assert hasattr(app.state, "generation_worker")

        assert worker.stopped
