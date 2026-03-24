"""Tests for SessionManager cleanup, LRU eviction, and patrol loop."""
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests.fakes import FakeSDKClient
from server.agent_runtime.session_manager import (
    ManagedSession,
    SessionManager,
    SessionCapacityError,
)
from server.agent_runtime.session_store import SessionMetaStore


def _make_manager(tmp_path: Path) -> SessionManager:
    """Create a SessionManager with a real MetaStore for testing."""
    return SessionManager(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        meta_store=SessionMetaStore(),
    )


def _make_managed(session_id: str = "s1", status="idle") -> ManagedSession:
    """Create a ManagedSession with a FakeSDKClient."""
    client = FakeSDKClient()
    managed = ManagedSession(session_id=session_id, client=client, status=status)
    managed.last_activity = time.monotonic()
    return managed


class _AssertingDisconnectClient:
    def __init__(self, manager: SessionManager, session_id: str):
        self.manager = manager
        self.session_id = session_id
        self.disconnected = False

    async def disconnect(self) -> None:
        assert self.session_id in self.manager.sessions
        self.disconnected = True


class _FakeProcess:
    def __init__(
        self,
        *,
        pid: int = 4321,
        terminate_returncode: int | None = None,
        kill_returncode: int | None = None,
    ):
        self.pid = pid
        self._returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self._wait_event = asyncio.Event()
        self._terminate_returncode = terminate_returncode
        self._kill_returncode = kill_returncode

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def wait(self) -> int:
        if self._returncode is None:
            await self._wait_event.wait()
        assert self._returncode is not None
        return self._returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self._terminate_returncode is not None:
            self._returncode = self._terminate_returncode
            self._wait_event.set()

    def kill(self) -> None:
        self.kill_calls += 1
        if self._kill_returncode is not None:
            self._returncode = self._kill_returncode
            self._wait_event.set()


class _HangingDisconnectClient:
    def __init__(self, process: _FakeProcess):
        self._transport = SimpleNamespace(_process=process)

    async def disconnect(self) -> None:
        await asyncio.sleep(3600)


class _ErrorDisconnectClientWithProcess:
    def __init__(self, process: _FakeProcess):
        self._transport = SimpleNamespace(_process=process)

    async def disconnect(self) -> None:
        raise RuntimeError("disconnect failed before transport close")


class _FailingDisconnectClient:
    async def disconnect(self) -> None:
        raise RuntimeError("disconnect failed")


class TestDisconnectSession:
    async def test_disconnect_removes_session_and_lock(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1")
        mgr.sessions["s1"] = managed
        mgr._connect_locks["s1"] = asyncio.Lock()

        await mgr._disconnect_session("s1")

        assert "s1" not in mgr.sessions
        assert "s1" not in mgr._connect_locks
        assert managed.client.disconnected is True

    async def test_disconnect_keeps_session_tracked_until_disconnect_finishes(self, tmp_path):
        mgr = _make_manager(tmp_path)
        client = _AssertingDisconnectClient(mgr, "s1")
        managed = ManagedSession(session_id="s1", client=client)
        mgr.sessions["s1"] = managed
        mgr._connect_locks["s1"] = asyncio.Lock()

        await mgr._disconnect_session("s1")

        assert client.disconnected is True
        assert "s1" not in mgr.sessions

    async def test_disconnect_cancels_cleanup_task(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1")
        managed._cleanup_task = asyncio.create_task(asyncio.sleep(9999))
        mgr.sessions["s1"] = managed

        await mgr._disconnect_session("s1")

        assert managed._cleanup_task.cancelled()

    async def test_disconnect_cancels_consumer_task(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1")
        managed.consumer_task = asyncio.create_task(asyncio.sleep(9999))
        mgr.sessions["s1"] = managed

        await mgr._disconnect_session("s1")

        assert managed.consumer_task.cancelled()

    async def test_disconnect_noop_for_missing_session(self, tmp_path):
        mgr = _make_manager(tmp_path)
        await mgr._disconnect_session("nonexistent")  # should not raise

    async def test_disconnect_force_terminates_stuck_claude_process(self, tmp_path):
        mgr = _make_manager(tmp_path)
        process = _FakeProcess(terminate_returncode=-15)
        managed = ManagedSession(
            session_id="s1",
            client=_HangingDisconnectClient(process),
            status="completed",
        )
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_DISCONNECT_TIMEOUT", 0.01):
            with patch.object(mgr, "_TERMINATE_WAIT_TIMEOUT", 0.01):
                await mgr._disconnect_session("s1")

        assert process.terminate_calls == 1
        assert process.kill_calls == 0
        assert "s1" not in mgr.sessions

    async def test_disconnect_force_kills_when_sigterm_cannot_stop_process(self, tmp_path):
        mgr = _make_manager(tmp_path)
        process = _FakeProcess(kill_returncode=-9)
        managed = ManagedSession(
            session_id="s1",
            client=_HangingDisconnectClient(process),
            status="completed",
        )
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_DISCONNECT_TIMEOUT", 0.01):
            with patch.object(mgr, "_TERMINATE_WAIT_TIMEOUT", 0.01):
                with patch.object(mgr, "_KILL_WAIT_TIMEOUT", 0.01):
                    await mgr._disconnect_session("s1")

        assert process.terminate_calls == 1
        assert process.kill_calls == 1
        assert "s1" not in mgr.sessions

    async def test_disconnect_exception_still_falls_back_to_process_cleanup(self, tmp_path):
        mgr = _make_manager(tmp_path)
        process = _FakeProcess(terminate_returncode=-15)
        managed = ManagedSession(
            session_id="s1",
            client=_ErrorDisconnectClientWithProcess(process),
            status="completed",
        )
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_TERMINATE_WAIT_TIMEOUT", 0.01):
            await mgr._disconnect_session("s1")

        assert process.terminate_calls == 1
        assert process.kill_calls == 0
        assert "s1" not in mgr.sessions


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
        """会话应在配置的延迟后被清理。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="completed")
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=1):
            mgr._schedule_cleanup("s1")
            await asyncio.sleep(1.5)

        assert "s1" not in mgr.sessions
        assert managed.client.disconnected is True

    async def test_cleanup_skips_if_session_resumed(self, tmp_path):
        """会话在清理前恢复为 running 则跳过。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="completed")
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=1):
            mgr._schedule_cleanup("s1")
            managed.status = "running"
            await asyncio.sleep(1.5)

        assert "s1" in mgr.sessions
        assert managed.client.disconnected is False

    async def test_cleanup_cancels_previous_task(self, tmp_path):
        """多次调度应取消旧的 cleanup task。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="completed")
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=9999):
            mgr._schedule_cleanup("s1")
            first_task = managed._cleanup_task
            mgr._schedule_cleanup("s1")
            second_task = managed._cleanup_task

        assert first_task is not second_task
        await asyncio.sleep(0)
        assert first_task.cancelled()
        second_task.cancel()

    async def test_finalize_turn_completed_schedules_cleanup(self, tmp_path):
        """_finalize_turn 产生 completed 状态时应调度 cleanup。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="running")
        mgr.sessions["s1"] = managed

        result_msg = {"type": "result", "subtype": "success", "is_error": False}

        with patch.object(mgr, "_schedule_cleanup") as mock_schedule:
            with patch.object(mgr.meta_store, "update_status", new_callable=AsyncMock):
                await mgr._finalize_turn(managed, result_msg)

        mock_schedule.assert_called_once_with("s1")
        assert managed.status == "completed"

    async def test_cleanup_task_cancelled_on_new_schedule(self, tmp_path):
        """error 状态的 cleanup task 在重新调度时应被取消。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="error")
        mgr.sessions["s1"] = managed

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


class TestEnsureCapacity:
    async def test_under_limit_no_eviction(self, tmp_path):
        """活跃数低于上限时不淘汰。"""
        mgr = _make_manager(tmp_path)
        mgr.sessions["s1"] = _make_managed("s1")

        with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=5):
            await mgr._ensure_capacity()

        assert "s1" in mgr.sessions

    async def test_evicts_oldest_non_running(self, tmp_path):
        """超限时淘汰最久未活跃的非 running 会话。"""
        mgr = _make_manager(tmp_path)
        old = _make_managed("s_old", status="idle")
        old.last_activity = time.monotonic() - 100
        new = _make_managed("s_new", status="idle")
        new.last_activity = time.monotonic()
        mgr.sessions["s_old"] = old
        mgr.sessions["s_new"] = new

        with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=2):
            with patch.object(mgr, "_disconnect_session", new_callable=AsyncMock) as mock_disc:
                await mgr._ensure_capacity()
                mock_disc.assert_called_once_with("s_old", reason="capacity eviction")

    async def test_evicts_completed_session_when_no_idle(self, tmp_path):
        """无 idle 会话时，应淘汰 completed/error/interrupted 状态的会话。"""
        mgr = _make_manager(tmp_path)
        completed = _make_managed("s_completed", status="completed")
        completed.last_activity = time.monotonic() - 50
        running = _make_managed("s_running", status="running")
        running.last_activity = time.monotonic()
        mgr.sessions["s_completed"] = completed
        mgr.sessions["s_running"] = running

        with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=2):
            with patch.object(mgr, "_disconnect_session", new_callable=AsyncMock) as mock_disc:
                await mgr._ensure_capacity()
                mock_disc.assert_called_once_with(
                    "s_completed",
                    reason="capacity eviction",
                )

    async def test_capacity_error_when_eviction_cannot_close_process(self, tmp_path):
        """淘汰失败时不应继续释放并发槽位。"""
        mgr = _make_manager(tmp_path)
        stuck = ManagedSession(
            session_id="s_stuck",
            client=_FailingDisconnectClient(),
            status="completed",
        )
        mgr.sessions["s_stuck"] = stuck

        with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=1):
            with pytest.raises(SessionCapacityError, match="未能关闭"):
                await mgr._ensure_capacity()

        assert "s_stuck" in mgr.sessions

    async def test_all_running_raises_capacity_error(self, tmp_path):
        """所有会话都在 running 时应抛出 SessionCapacityError。"""
        mgr = _make_manager(tmp_path)
        for i in range(3):
            mgr.sessions[f"s{i}"] = _make_managed(f"s{i}", status="running")

        with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=3):
            with pytest.raises(SessionCapacityError, match="正在进行的会话"):
                await mgr._ensure_capacity()

    async def test_capacity_error_message_includes_count(self, tmp_path):
        """错误消息中应包含当前 running 会话数。"""
        mgr = _make_manager(tmp_path)
        for i in range(3):
            mgr.sessions[f"s{i}"] = _make_managed(f"s{i}", status="running")

        with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=3):
            with pytest.raises(SessionCapacityError, match="3个"):
                await mgr._ensure_capacity()


class TestPatrolLoop:
    async def test_patrol_cleans_stale_session(self, tmp_path):
        """巡检应清理超时的非 running 会话。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="completed")
        managed.last_activity = time.monotonic() - 1000
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=60):
            with patch.object(mgr, "_disconnect_session", new_callable=AsyncMock) as mock_disc:
                await mgr._patrol_once()
                mock_disc.assert_called_once_with("s1", reason="patrol cleanup")

    async def test_patrol_skips_running(self, tmp_path):
        """巡检不应清理 running 会话。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="running")
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=60):
            with patch.object(mgr, "_disconnect_session", new_callable=AsyncMock) as mock_disc:
                await mgr._patrol_once()
                mock_disc.assert_not_called()

    async def test_patrol_skips_recent_session(self, tmp_path):
        """巡检不应清理近期活跃的会话。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="completed")
        managed.last_activity = time.monotonic()  # 刚刚活跃
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=600):
            with patch.object(mgr, "_disconnect_session", new_callable=AsyncMock) as mock_disc:
                await mgr._patrol_once()
                mock_disc.assert_not_called()
