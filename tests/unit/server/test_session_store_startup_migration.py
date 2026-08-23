"""Lifespan should invoke session-store transcript migration once."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_lifespan_invokes_session_store_migration(tmp_path):
    """The session-store migration must be called exactly once during startup."""
    # We don't want a real lifespan to fire all its long-running side effects
    # (worker starts, http client, project event service). Patch them all out
    # and only verify our new hook is wired in.
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
        # 数据目录指向空的 tmp 目录：源文编码迁移照跑，遍历不到项目即返回空汇总。
        patch("server.app.app_data_dir", return_value=tmp_path),
        patch("server.app.startup_http_client", new=AsyncMock(return_value=None)),
        patch("server.app.shutdown_http_client", new=AsyncMock(return_value=None)),
        patch("server.app.create_generation_worker") as worker_factory,
        patch("server.app.assistant.assistant_service") as svc_mock,
        patch("server.app.ProjectEventService") as pes_factory,
        patch("server.app.close_db", new=AsyncMock(return_value=None)),
    ):
        # Mock the worker so its start() / stop() are awaitables, no-op.
        worker_factory.return_value = type(
            "W",
            (),
            {
                "start": AsyncMock(),
                "stop": AsyncMock(),
                "request_cancel": lambda self, _tid: False,
            },
        )()
        # assistant_service.startup is awaited
        svc_mock.startup = AsyncMock()
        svc_mock.session_manager = type(
            "S",
            (),
            {
                "start_patrol": lambda self: None,
                "stop_patrol": lambda self: None,
            },
        )()
        # project_event_service.start() / shutdown()
        pes_factory.return_value = type(
            "P",
            (),
            {
                "start": AsyncMock(),
                "shutdown": AsyncMock(),
            },
        )()

        from server.app import app, lifespan

        async with lifespan(app):
            pass

    migrate_mock.assert_called_once()
    args, kwargs = migrate_mock.call_args
    # Sanity: store should be passed as positional arg, projects_root + data_dir as kwargs
    assert "projects_root" in kwargs
    assert "data_dir" in kwargs
