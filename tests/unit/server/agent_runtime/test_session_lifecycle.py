"""Tests for SessionManager cleanup, LRU eviction, and patrol loop."""

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from server.agent_runtime.session_actor import SessionActor
from server.agent_runtime.session_manager import (
    ManagedSession,
    SessionCapacityError,
    SessionManager,
)
from server.agent_runtime.session_store import SessionMetaStore
from tests.fakes import FakeSDKClient, session_state_message


def _make_manager(tmp_path: Path) -> SessionManager:
    """Create a SessionManager with a real MetaStore for testing."""
    return SessionManager(
        project_root=tmp_path,
        meta_store=SessionMetaStore(),
    )


def _make_managed(session_id: str = "s1", status="idle") -> tuple[ManagedSession, FakeSDKClient]:
    """Build a ManagedSession wrapped around a started SessionActor + FakeSDKClient.

    Returned tuple: (managed, client) so tests can assert on ``client.disconnected``.
    """
    client = FakeSDKClient()

    @asynccontextmanager
    async def _factory():
        async with client as c:
            yield c

    actor = SessionActor(client_factory=_factory, on_message=lambda msg: None)
    managed = ManagedSession(session_id=session_id, actor=actor, status=status, project_name="demo")
    managed.last_activity = time.monotonic()
    return managed, client


async def _start(managed: ManagedSession) -> ManagedSession:
    """Start the actor attached to *managed*. Call from an async test."""
    await managed.actor.start()
    return managed


class TestCloseSession:
    async def test_close_removes_session_and_lock(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed
        mgr._connect_locks["s1"] = asyncio.Lock()

        await mgr.close_session("s1")

        assert "s1" not in mgr.sessions
        assert "s1" not in mgr._connect_locks
        assert client.disconnected is True

    async def test_close_cancels_cleanup_task(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1")
        await _start(managed)
        managed._cleanup_task = asyncio.create_task(asyncio.sleep(9999))
        mgr.sessions["s1"] = managed

        await mgr.close_session("s1")

        assert managed._cleanup_task.cancelled()

    async def test_close_cancels_process_task(self, tmp_path):
        """_evict_one drains the inbox processor so _process_task finishes."""
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1")
        await _start(managed)
        managed._process_task = asyncio.create_task(mgr._process_inbox(managed))
        mgr.sessions["s1"] = managed

        await mgr.close_session("s1")

        assert managed._process_task.done()

    async def test_close_noop_for_missing_session(self, tmp_path):
        """Closing an unregistered session is a no-op: neither raise nor affect other sessions."""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed

        try:
            await mgr.close_session("nonexistent")

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")


class TestConfigReading:
    async def test_get_cleanup_delay_default(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch("server.agent_runtime.session_manager.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("server.agent_runtime.session_manager.ConfigService") as MockSvc:
                MockSvc.return_value.get_setting = AsyncMock(return_value="300")
                result = await mgr._get_cleanup_delay()
        assert result == 300

    async def test_get_max_concurrent_default(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch("server.agent_runtime.session_manager.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("server.agent_runtime.session_manager.ConfigService") as MockSvc:
                MockSvc.return_value.get_setting = AsyncMock(return_value="5")
                result = await mgr._get_max_concurrent()
        assert result == 5


class TestCleanup:
    async def test_cleanup_disconnects_after_delay(self, tmp_path):
        """Clean sessions after the configured delay."""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1", status="completed")
        await _start(managed)
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=1):
            mgr._schedule_cleanup("s1")
            await asyncio.sleep(1.5)

        assert "s1" not in mgr.sessions
        assert client.disconnected is True

    async def test_cleanup_skips_if_session_resumed(self, tmp_path):
        """Skip cleanup if the session resumes running first."""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1", status="completed")
        await _start(managed)
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=1):
                mgr._schedule_cleanup("s1")
                managed.status = "running"
                await asyncio.sleep(1.5)

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            managed.status = "idle"
            await mgr.close_session("s1")

    async def test_cleanup_cancels_previous_task(self, tmp_path):
        """Repeated scheduling cancels the old cleanup task."""
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1", status="completed")
        await _start(managed)
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=9999):
                mgr._schedule_cleanup("s1")
                first_task = managed._cleanup_task
                mgr._schedule_cleanup("s1")
                second_task = managed._cleanup_task

            assert first_task is not second_task
            await asyncio.sleep(0)
            assert first_task.cancelled()
            second_task.cancel()
        finally:
            await mgr.close_session("s1")

    async def test_result_alone_neither_settles_nor_schedules_cleanup(self, tmp_path):
        """result ends one turn only: keep running without scheduling cleanup until CLI reports idle."""
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1", status="running")
        await _start(managed)
        mgr.sessions["s1"] = managed

        result_msg = {"type": "result", "subtype": "success", "is_error": False}

        try:
            with (
                patch.object(mgr, "_schedule_cleanup") as mock_schedule,
                patch.object(mgr.meta_store, "update_status", new_callable=AsyncMock),
            ):
                await mgr._finalize_turn(managed, result_msg)

            mock_schedule.assert_not_called()
            assert managed.status == "running"
        finally:
            await mgr.close_session("s1")

    async def test_cleanup_task_cancelled_on_new_schedule(self, tmp_path):
        """Rescheduling cancels cleanup tasks for error sessions."""
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1", status="error")
        await _start(managed)
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=9999):
                mgr._schedule_cleanup("s1")
                first_task = managed._cleanup_task
                managed.status = "completed"
                mgr._schedule_cleanup("s1")
                second_task = managed._cleanup_task

            assert first_task is not second_task
            await asyncio.sleep(0)
            assert first_task.cancelled()
            second_task.cancel()
        finally:
            await mgr.close_session("s1")


class TestEnsureCapacity:
    async def test_under_limit_no_eviction(self, tmp_path):
        """Do not evict when active count is below capacity."""
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=5):
                await mgr._ensure_capacity()

            assert "s1" in mgr.sessions
        finally:
            await mgr.close_session("s1")

    async def test_evicts_oldest_non_running(self, tmp_path):
        """Above capacity, evict the longest-inactive nonrunning session."""
        mgr = _make_manager(tmp_path)
        old, old_client = _make_managed("s_old", status="idle")
        await _start(old)
        old.last_activity = time.monotonic() - 100
        new, new_client = _make_managed("s_new", status="idle")
        await _start(new)
        new.last_activity = time.monotonic()
        mgr.sessions["s_old"] = old
        mgr.sessions["s_new"] = new

        try:
            with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=2):
                await mgr._ensure_capacity()

            assert "s_old" not in mgr.sessions
            assert old_client.disconnected is True
            assert "s_new" in mgr.sessions
            assert new_client.disconnected is False
        finally:
            await mgr.close_session("s_old")
            await mgr.close_session("s_new")

    async def test_skips_session_once_cli_reports_work_before_the_inbox_runs(self, tmp_path):
        """CLI starts a turn autonomously and its running frame has been read but not yet processed by inbox: the session is already active and must not disconnect."""
        mgr = _make_manager(tmp_path)
        busy, busy_client = _make_managed("s_busy", status="idle")
        await _start(busy)
        mgr._make_actor_message_callback([busy])(session_state_message("running"))
        busy.last_activity = time.monotonic() - 100
        quiet, quiet_client = _make_managed("s_quiet", status="idle")
        await _start(quiet)
        quiet.last_activity = time.monotonic()
        mgr.sessions["s_busy"] = busy
        mgr.sessions["s_quiet"] = quiet

        try:
            with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=2):
                await mgr._ensure_capacity()

            assert busy_client.disconnected is False
            assert quiet_client.disconnected is True
        finally:
            await mgr.close_session("s_busy")
            await mgr.close_session("s_quiet")

    async def test_evicts_completed_session_when_no_idle(self, tmp_path):
        """Without idle sessions, evict completed/error/interrupted sessions."""
        mgr = _make_manager(tmp_path)
        completed, completed_client = _make_managed("s_completed", status="completed")
        await _start(completed)
        completed.last_activity = time.monotonic() - 50
        running, running_client = _make_managed("s_running", status="running")
        await _start(running)
        running.last_activity = time.monotonic()
        mgr.sessions["s_completed"] = completed
        mgr.sessions["s_running"] = running

        try:
            with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=2):
                await mgr._ensure_capacity()

            assert "s_completed" not in mgr.sessions
            assert completed_client.disconnected is True
            assert "s_running" in mgr.sessions
            assert running_client.disconnected is False
        finally:
            await mgr.close_session("s_completed")
            await mgr.close_session("s_running")

    async def test_all_running_raises_capacity_error(self, tmp_path):
        """If every session is running, raise SessionCapacityError."""
        mgr = _make_manager(tmp_path)
        managed_list: list[ManagedSession] = []
        for i in range(3):
            m, _ = _make_managed(f"s{i}", status="running")
            await _start(m)
            managed_list.append(m)
            mgr.sessions[f"s{i}"] = m

        try:
            with (
                patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=3),
                pytest.raises(SessionCapacityError, match="正在进行的会话"),
            ):
                await mgr._ensure_capacity()
        finally:
            for i in range(3):
                await mgr.close_session(f"s{i}")

    async def test_capacity_error_message_includes_count(self, tmp_path):
        """Error wording includes the current running-session count."""
        mgr = _make_manager(tmp_path)
        for i in range(3):
            m, _ = _make_managed(f"s{i}", status="running")
            await _start(m)
            mgr.sessions[f"s{i}"] = m

        try:
            with (
                patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=3),
                pytest.raises(SessionCapacityError, match="3个"),
            ):
                await mgr._ensure_capacity()
        finally:
            for i in range(3):
                await mgr.close_session(f"s{i}")


class TestPatrolLoop:
    async def test_patrol_cleans_stale_session(self, tmp_path):
        """Inspection cleans timed-out nonrunning sessions."""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1", status="completed")
        await _start(managed)
        managed.last_activity = time.monotonic() - 1000
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=60):
                await mgr._patrol_once()

            assert "s1" not in mgr.sessions
            assert client.disconnected is True
        finally:
            await mgr.close_session("s1")

    async def test_patrol_skips_running(self, tmp_path):
        """Inspection never cleans running sessions."""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1", status="running")
        await _start(managed)
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=60):
                await mgr._patrol_once()

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")

    async def test_patrol_skips_recent_session(self, tmp_path):
        """Inspection never cleans recently active sessions."""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1", status="completed")
        await _start(managed)
        managed.last_activity = time.monotonic()  # Just active.
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=600):
                await mgr._patrol_once()

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")


# --- Nonidle sessions: disconnecting CLI before it reports idle interrupts in-flight turns or background subagents between turns. ------


_RESULT = {"type": "result", "subtype": "success", "is_error": False}


async def _feed(mgr: SessionManager, managed: ManagedSession, *frames: dict) -> None:
    """Feed message frames through real read callbacks and inbox processing; return after processing."""
    managed.resolved_sdk_id = managed.session_id
    on_message = mgr._make_actor_message_callback([managed])
    for frame in frames:
        on_message(frame)
    managed._inbox.put_nowait(None)
    await mgr._process_inbox(managed)


class TestNotIdleSessionProtection:
    @pytest.mark.parametrize("state", ["running", "requires_action"])
    async def test_idle_cleanup_spares_session_after_result_until_cli_reports_idle(self, tmp_path, state):
        """After a turn result, CLI may still work (background subagents remain or another turn is owed); the session is not idle and must not disconnect."""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=0):
                await _feed(mgr, managed, session_state_message(state), _RESULT)
                await mgr._cleanup_idle("s1")

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")

    async def test_cli_reporting_idle_starts_idle_cleanup(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed

        actor_task = managed.actor.task
        assert actor_task is not None
        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=0):
                await _feed(mgr, managed, session_state_message("running"), _RESULT, session_state_message("idle"))
                # Cleanup disconnect exits the actor; shield makes timeout report failure without also cancelling the actor.
                await asyncio.wait_for(asyncio.shield(actor_task), timeout=1.0)

            assert client.disconnected is True
        finally:
            await mgr.close_session("s1")

    async def test_capacity_rejects_rather_than_evicting_a_session_that_is_not_idle(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed
        await _feed(mgr, managed, session_state_message("running"), _RESULT)

        try:
            with (
                patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=1),
                pytest.raises(SessionCapacityError),
            ):
                await mgr._ensure_capacity()

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")

    async def test_capacity_evicts_idle_session_before_older_one_that_is_not_idle(self, tmp_path):
        mgr = _make_manager(tmp_path)
        busy, busy_client = _make_managed("s_busy")
        await _start(busy)
        mgr.sessions["s_busy"] = busy
        await _feed(mgr, busy, session_state_message("running"), _RESULT)
        busy.last_activity = time.monotonic() - 100
        quiet, quiet_client = _make_managed("s_quiet")
        await _start(quiet)
        mgr.sessions["s_quiet"] = quiet

        try:
            with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=2):
                await mgr._ensure_capacity()

            assert quiet_client.disconnected is True
            assert busy_client.disconnected is False
        finally:
            await mgr.close_session("s_busy")
            await mgr.close_session("s_quiet")

    async def test_patrol_spares_stale_session_that_is_not_idle(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1", status="completed")
        await _start(managed)
        mgr.sessions["s1"] = managed
        await _feed(mgr, managed, session_state_message("running"), _RESULT)
        managed.last_activity = time.monotonic() - 1000

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=60):
                await mgr._patrol_once()

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")

    async def test_patrol_counts_idle_time_from_when_cli_reported_idle(self, tmp_path):
        """If background work outlasts twice the cleanup delay, reporting idle must not let inspection immediately evict by activity time predating that work."""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1", status="completed")
        await _start(managed)
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=60):
                await _feed(mgr, managed, session_state_message("running"), _RESULT)
                managed.last_activity = time.monotonic() - 1000
                await _feed(mgr, managed, session_state_message("idle"))
                await mgr._patrol_once()

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")

    async def test_cli_exit_settles_the_session_and_releases_protection(self, tmp_path):
        """CLI exit emits no later idle: finalize normally rather than occupying capacity forever."""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        actor_task = managed.actor.task
        assert actor_task is not None
        actor_task.add_done_callback(mgr._make_actor_done_callback(managed))
        mgr.sessions["s1"] = managed
        managed.resolved_sdk_id = "s1"
        on_message = mgr._make_actor_message_callback([managed])
        on_message(session_state_message("running"))

        client.close_stream()
        await asyncio.wait_for(mgr._process_inbox(managed), timeout=1.0)
        assert managed.status == "error"

        with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=0):
            await mgr._cleanup_idle("s1")
        assert "s1" not in mgr.sessions
