"""Lifespan should invoke session-store transcript migration once."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lib.project.project_manager import ProjectManager


@pytest.mark.asyncio
async def test_lifespan_invokes_session_store_migration(tmp_path, monkeypatch):
    """The session-store migration must be called exactly once during startup."""
    from tests.fakes import use_project_manager

    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    use_project_manager(monkeypatch, ProjectManager(tmp_path))
    with (
        patch(
            "server.app.migrate_local_transcripts_to_store",
            new=AsyncMock(return_value={"imported": 0, "skipped": 0, "failed": 0}),
        ) as migrate_mock,
        patch("server.app.init_db", new=AsyncMock(return_value=None)),
        patch(
            "server.app.run_project_migrations",
            return_value=type("M", (), {"migrated": [], "failed": [], "skipped": []})(),
        ),
        patch("server.app.cleanup_stale_backups"),
        patch("server.app.startup_http_client", new=AsyncMock(return_value=None)),
        patch("server.app.shutdown_http_client", new=AsyncMock(return_value=None)),
        patch("server.app.create_generation_worker") as worker_factory,
        patch("server.app.assistant.assistant_service") as svc_mock,
        patch("server.app.ProjectEventService") as pes_factory,
        patch("server.app.close_db", new=AsyncMock(return_value=None)),
    ):
        worker_factory.return_value = type(
            "W",
            (),
            {
                "start": AsyncMock(),
                "stop": AsyncMock(),
                "stop_admission": lambda self: None,
            },
        )()
        svc_mock.startup = AsyncMock()
        svc_mock.shutdown = AsyncMock()
        svc_mock.session_manager = type(
            "S",
            (),
            {
                "start_patrol": lambda self: None,
                "reset_streams": lambda self: None,
                "stop_streams": lambda self: None,
                "stop_patrol": lambda self: None,
                "set_autonomous_turn_listener": lambda self, listener: None,
            },
        )()
        pes_factory.return_value = type(
            "P",
            (),
            {
                "start": AsyncMock(),
                "shutdown": AsyncMock(),
                "stop_streams": lambda self: None,
                "publish_assistant_session_resumed": lambda self, project_name, session_id: None,
            },
        )()

        from server.app import app, lifespan

        async with lifespan(app):
            pass

    migrate_mock.assert_called_once()
    _args, kwargs = migrate_mock.call_args
    assert "data_root" in kwargs
