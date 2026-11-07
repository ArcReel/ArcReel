"""Shared generation-worker integration fixtures."""

from pathlib import Path

import pytest

from tests.fakes import bind_safe_session_factory


@pytest.fixture
async def worker_db(db_factory, monkeypatch):
    """Redirect worker-direct global session factories to the in-memory DB.

    ``_requeue_single_task`` and ``CapacityTable.from_db`` bypass injected queues and use module-imported ``safe_session_factory``. Binding it to memory runs both real paths, allowing assertions on persisted row states."""
    bind_safe_session_factory(monkeypatch, db_factory)
    return db_factory


@pytest.fixture
def staged_project(tmp_path, monkeypatch) -> Path:
    """Point worker project lookup at temporary projects so staging cleanup uses real filesystems."""
    project_path = tmp_path / "demo"
    project_path.mkdir()

    class _PM:
        def get_project_path(self, _name: str) -> Path:
            return project_path

    monkeypatch.setattr("lib.generation.video_resume.get_project_manager", lambda: _PM())
    return project_path
