"""Shared startup-dependency substitutes for FastAPI lifespan tests."""

from __future__ import annotations

import lib.db
import server.app as app_module
from server.routers import assistant as assistant_router


async def _noop_async(*args, **kwargs):
    """No-op coroutine for mocking async startup steps."""


class _FakeWorker:
    def __init__(self):
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


def stub_lifespan_dependencies(monkeypatch) -> _FakeWorker:
    """Replace lifespan dependencies unrelated to the tested step (database creation, authentication password, generation worker, assistant service) with no-ops; return the worker substitute."""
    worker = _FakeWorker()
    monkeypatch.setattr(app_module, "create_generation_worker", lambda: worker)
    monkeypatch.setattr(app_module, "ensure_auth_password", lambda: "test")
    monkeypatch.setattr(app_module, "init_db", _noop_async)
    monkeypatch.setattr(lib.db, "init_db", _noop_async)
    monkeypatch.setattr(assistant_router.assistant_service, "startup", _noop_async)
    monkeypatch.setattr(assistant_router.assistant_service, "shutdown", _noop_async)
    return worker
