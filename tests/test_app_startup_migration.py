"""FastAPI 启动时调用 run_project_migrations 和 cleanup_stale_backups。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import lib.db
import server.app as app_module
from server.routers import assistant as assistant_router


async def _noop_async(*args, **kwargs):
    """No-op coroutine for mocking async startup steps."""


class _FakeWorker:
    async def start(self):
        pass

    async def stop(self):
        pass


@pytest.mark.asyncio
async def test_startup_invokes_project_migrations(monkeypatch):
    run_mock = MagicMock(return_value=SimpleNamespace(migrated=[], skipped=[], failed=[]))
    cleanup_mock = MagicMock()

    monkeypatch.setattr(app_module, "run_project_migrations", run_mock)
    monkeypatch.setattr(app_module, "cleanup_stale_backups", cleanup_mock)
    monkeypatch.setattr(app_module, "ensure_auth_password", lambda: "test")
    monkeypatch.setattr(app_module, "init_db", _noop_async)
    monkeypatch.setattr(lib.db, "init_db", _noop_async)
    monkeypatch.setattr(app_module, "create_generation_worker", _FakeWorker)
    monkeypatch.setattr(assistant_router.assistant_service, "startup", _noop_async)
    monkeypatch.setattr(assistant_router.assistant_service, "shutdown", _noop_async)
    # Avoid touching real on-disk projects/ during a unit test that fires lifespan.
    monkeypatch.setattr(app_module, "migrate_local_transcripts_to_store", _noop_async)

    app = app_module.app
    app.state = SimpleNamespace()

    async with app_module.lifespan(app):
        pass

    run_mock.assert_called_once()
    cleanup_mock.assert_called_once()
