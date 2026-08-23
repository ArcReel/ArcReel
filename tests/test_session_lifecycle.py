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
from tests.fakes import FakeSDKClient

pytestmark = pytest.mark.unit


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
        mgr = _make_manager(tmp_path)
        await mgr.close_session("nonexistent")  # should not raise


class TestConfigReading:
    def test_subagent_stall_timeout_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ASSISTANT_SUBAGENT_STALL_TIMEOUT_SECONDS", "180")
        mgr = _make_manager(tmp_path)
        assert mgr.subagent_stall_timeout_seconds == 180

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
        """会话应在配置的延迟后被清理。"""
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
        """会话在清理前恢复为 running 则跳过。"""
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
        """多次调度应取消旧的 cleanup task。"""
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

    async def test_finalize_turn_completed_schedules_cleanup(self, tmp_path):
        """_finalize_turn 产生 completed 状态时应调度 cleanup。"""
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1", status="running")
        await _start(managed)
        mgr.sessions["s1"] = managed

        result_msg = {"type": "result", "subtype": "success", "is_error": False}

        try:
            with patch.object(mgr, "_schedule_cleanup") as mock_schedule:
                with patch.object(mgr.meta_store, "update_status", new_callable=AsyncMock):
                    await mgr._finalize_turn(managed, result_msg)

            mock_schedule.assert_called_once_with("s1")
            assert managed.status == "completed"
        finally:
            await mgr.close_session("s1")

    async def test_async_subagent_defers_parent_cleanup_until_notification(self, tmp_path):
        """父轮次完成后 runtime 继续承载异步子任务；终态通知到达才开始清理倒计时。"""
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1", status="running")
        await _start(managed)
        mgr.sessions["s1"] = managed
        launch = {
            "type": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "launched"}],
            "tool_use_result": {"isAsync": True, "status": "async_launched", "agentId": "agent-1"},
        }
        notification = {
            "type": "system",
            "subtype": "task_notification",
            "task_id": "agent-1",
            "tool_use_id": "tu-1",
            "status": "completed",
        }

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=9999):
                mgr._handle_special_message(managed, launch)
                with patch.object(mgr.meta_store, "update_status", new_callable=AsyncMock):
                    await mgr._finalize_turn(
                        managed,
                        {"type": "result", "subtype": "success", "is_error": False},
                    )

                assert managed.status == "completed"
                assert managed.active_subagents == {"tu-1": "agent-1"}
                assert managed._cleanup_task is None
                assert mgr.subagent_runtime_alive("s1") is True

                mgr._handle_special_message(managed, notification)
                assert managed.active_subagents == {}
                assert managed._cleanup_task is not None
                assert mgr.subagent_runtime_alive("s1") is False
        finally:
            await mgr.close_session("s1")

    async def test_xml_task_notification_releases_deferred_cleanup(self, tmp_path):
        """SDK 注入的 XML 通知与 typed 通知走同一生命周期收口。"""
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1", status="completed")
        await _start(managed)
        mgr.sessions["s1"] = managed
        managed.active_subagents["tu-1"] = "agent-1"
        xml = (
            "<task-notification><task-id>agent-1</task-id><tool-use-id>tu-1</tool-use-id>"
            "<status>completed</status><summary>完成</summary></task-notification>"
        )

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=9999):
                mgr._handle_special_message(managed, {"type": "user", "content": xml})
                assert managed.active_subagents == {}
                assert managed._cleanup_task is not None
        finally:
            await mgr.close_session("s1")

    async def test_capacity_does_not_evict_completed_parent_with_active_subagent(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1", status="completed")
        await _start(managed)
        managed.active_subagents["tu-1"] = "agent-1"
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=1):
                with pytest.raises(SessionCapacityError):
                    await mgr._ensure_capacity()

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")

    async def test_cleanup_task_cancelled_on_new_schedule(self, tmp_path):
        """error 状态的 cleanup task 在重新调度时应被取消。"""
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
        """活跃数低于上限时不淘汰。"""
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
        """超限时淘汰最久未活跃的非 running 会话。"""
        mgr = _make_manager(tmp_path)
        old, _ = _make_managed("s_old", status="idle")
        await _start(old)
        old.last_activity = time.monotonic() - 100
        new, _ = _make_managed("s_new", status="idle")
        await _start(new)
        new.last_activity = time.monotonic()
        mgr.sessions["s_old"] = old
        mgr.sessions["s_new"] = new

        with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=2):
            with patch.object(mgr, "_evict_one", new_callable=AsyncMock) as mock_evict:
                await mgr._ensure_capacity()
                mock_evict.assert_called_once_with(old)

        await mgr.close_session("s_old")
        await mgr.close_session("s_new")

    async def test_evicts_completed_session_when_no_idle(self, tmp_path):
        """无 idle 会话时，应淘汰 completed/error/interrupted 状态的会话。"""
        mgr = _make_manager(tmp_path)
        completed, _ = _make_managed("s_completed", status="completed")
        await _start(completed)
        completed.last_activity = time.monotonic() - 50
        running, _ = _make_managed("s_running", status="running")
        await _start(running)
        running.last_activity = time.monotonic()
        mgr.sessions["s_completed"] = completed
        mgr.sessions["s_running"] = running

        with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=2):
            with patch.object(mgr, "_evict_one", new_callable=AsyncMock) as mock_evict:
                await mgr._ensure_capacity()
                mock_evict.assert_called_once_with(completed)

        await mgr.close_session("s_completed")
        await mgr.close_session("s_running")

    async def test_all_running_raises_capacity_error(self, tmp_path):
        """所有会话都在 running 时应抛出 SessionCapacityError。"""
        mgr = _make_manager(tmp_path)
        managed_list: list[ManagedSession] = []
        for i in range(3):
            m, _ = _make_managed(f"s{i}", status="running")
            await _start(m)
            managed_list.append(m)
            mgr.sessions[f"s{i}"] = m

        try:
            with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=3):
                with pytest.raises(SessionCapacityError, match="正在进行的会话"):
                    await mgr._ensure_capacity()
        finally:
            for i in range(3):
                await mgr.close_session(f"s{i}")

    async def test_capacity_error_message_includes_count(self, tmp_path):
        """错误消息中应包含当前 running 会话数。"""
        mgr = _make_manager(tmp_path)
        for i in range(3):
            m, _ = _make_managed(f"s{i}", status="running")
            await _start(m)
            mgr.sessions[f"s{i}"] = m

        try:
            with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=3):
                with pytest.raises(SessionCapacityError, match="3个"):
                    await mgr._ensure_capacity()
        finally:
            for i in range(3):
                await mgr.close_session(f"s{i}")


class TestPatrolLoop:
    async def test_patrol_cleans_stale_session(self, tmp_path):
        """巡检应清理超时的非 running 会话。"""
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1", status="completed")
        await _start(managed)
        managed.last_activity = time.monotonic() - 1000
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=60):
            with patch.object(mgr, "_evict_one", new_callable=AsyncMock) as mock_evict:
                await mgr._patrol_once()
                mock_evict.assert_called_once_with(managed)

        await mgr.close_session("s1")

    async def test_patrol_skips_running(self, tmp_path):
        """巡检不应清理 running 会话。"""
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1", status="running")
        await _start(managed)
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=60):
                with patch.object(mgr, "_evict_one", new_callable=AsyncMock) as mock_evict:
                    await mgr._patrol_once()
                    mock_evict.assert_not_called()
        finally:
            await mgr.close_session("s1")

    async def test_patrol_skips_completed_parent_with_active_subagent(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1", status="completed")
        await _start(managed)
        managed.last_activity = time.monotonic() - 1000
        managed.active_subagents["tu-1"] = "agent-1"
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=60):
                with patch.object(mgr, "_evict_one", new_callable=AsyncMock) as mock_evict:
                    await mgr._patrol_once()
                    mock_evict.assert_not_called()
        finally:
            await mgr.close_session("s1")

    async def test_patrol_skips_recent_session(self, tmp_path):
        """巡检不应清理近期活跃的会话。"""
        mgr = _make_manager(tmp_path)
        managed, _ = _make_managed("s1", status="completed")
        await _start(managed)
        managed.last_activity = time.monotonic()  # 刚刚活跃
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=600):
                with patch.object(mgr, "_evict_one", new_callable=AsyncMock) as mock_evict:
                    await mgr._patrol_once()
                    mock_evict.assert_not_called()
        finally:
            await mgr.close_session("s1")


class TestSubagentStallWatchdog:
    @staticmethod
    def _launch(managed: ManagedSession, key: str, task_id: str) -> None:
        SessionManager._sync_subagent_lifecycle(
            object.__new__(SessionManager),
            managed,
            {
                "type": "user",
                "content": [{"type": "tool_result", "tool_use_id": key, "content": "launched"}],
                "tool_use_result": {"agentId": task_id, "status": "async_launched"},
            },
        )

    async def test_watchdog_stops_only_task_without_token_growth(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.subagent_stall_timeout_seconds = 300
        managed, _ = _make_managed("s1", status="completed")
        await _start(managed)
        mgr.sessions["s1"] = managed
        self._launch(managed, "tu-stale", "agent-stale")
        self._launch(managed, "tu-live", "agent-live")
        managed.subagent_activity["tu-stale"].last_token_change = 100.0
        managed.subagent_activity["tu-live"].last_token_change = 350.0
        managed.send_stop_task = AsyncMock()

        try:
            await mgr._subagent_watchdog_once(now=401.0)

            managed.send_stop_task.assert_awaited_once_with("agent-stale")
            assert managed.subagent_activity["tu-stale"].stalled is True
            assert managed.subagent_activity["tu-live"].stalled is False
            _revision, stalled_ids, timeout = mgr.subagent_projection_state("s1")
            assert stalled_ids == {"agent-stale"}
            assert timeout == 300

            mgr._sync_subagent_lifecycle(
                managed,
                {
                    "type": "system",
                    "subtype": "task_updated",
                    "task_id": "agent-stale",
                    "patch": {"status": "killed"},
                },
            )
            assert managed.active_subagents == {"tu-live": "agent-live"}
        finally:
            await mgr.close_session("s1")

    async def test_token_growth_resets_watchdog_deadline(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.subagent_stall_timeout_seconds = 300
        managed, _ = _make_managed("s1", status="completed")
        await _start(managed)
        mgr.sessions["s1"] = managed
        self._launch(managed, "tu-1", "agent-1")
        managed.subagent_activity["tu-1"].last_token_change = 100.0
        managed.send_stop_task = AsyncMock()

        try:
            with patch("server.agent_runtime.session_manager.time.monotonic", return_value=350.0):
                mgr._sync_subagent_lifecycle(
                    managed,
                    {
                        "type": "assistant",
                        "parent_tool_use_id": "tu-1",
                        "message_id": "msg-1",
                        "usage": {"input_tokens": 100, "output_tokens": 20},
                        "content": [],
                    },
                )
            await mgr._subagent_watchdog_once(now=500.0)

            managed.send_stop_task.assert_not_awaited()
            assert managed.subagent_activity["tu-1"].total_tokens == 120
            assert managed.subagent_activity["tu-1"].last_token_change == 350.0
        finally:
            await mgr.close_session("s1")
