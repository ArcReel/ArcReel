"""Tests for SessionManager idle TTL, LRU eviction, and patrol loop."""
import asyncio
import time
from pathlib import Path
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

    async def test_disconnect_cancels_idle_cleanup_task(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1")
        managed._idle_cleanup_task = asyncio.create_task(asyncio.sleep(9999))
        mgr.sessions["s1"] = managed

        await mgr._disconnect_session("s1")

        assert managed._idle_cleanup_task.cancelled()

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


class TestConfigReading:
    async def test_get_idle_ttl_default(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch("server.agent_runtime.session_manager.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("server.agent_runtime.session_manager.ConfigService") as MockSvc:
                MockSvc.return_value.get_setting = AsyncMock(return_value="10")
                result = await mgr._get_idle_ttl()
        assert result == 600  # 10 minutes in seconds

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


class TestIdleCleanup:
    async def test_idle_cleanup_disconnects_after_ttl(self, tmp_path):
        """TTL 到期后 idle 会话应被清理。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="idle")
        managed.idle_since = time.monotonic() - 10  # 已 idle 10 秒
        mgr.sessions["s1"] = managed

        # 用极短 TTL 触发 — _get_idle_ttl is async, use AsyncMock
        with patch.object(mgr, "_get_idle_ttl", new_callable=AsyncMock, return_value=1):
            mgr._schedule_idle_cleanup("s1")
            await asyncio.sleep(1.5)

        assert "s1" not in mgr.sessions
        assert managed.client.disconnected is True

    async def test_idle_cleanup_skips_if_session_resumed(self, tmp_path):
        """用户在 TTL 到期前发送消息，会话不应被清理。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="idle")
        old_idle_since = time.monotonic()
        managed.idle_since = old_idle_since
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_idle_ttl", new_callable=AsyncMock, return_value=1):
            mgr._schedule_idle_cleanup("s1")
            # 模拟用户发送新消息：idle_since 被刷新
            managed.idle_since = time.monotonic() + 100
            managed.status = "running"
            await asyncio.sleep(1.5)

        assert "s1" in mgr.sessions
        assert managed.client.disconnected is False

    async def test_idle_cleanup_cancels_previous_task(self, tmp_path):
        """多次调度应取消旧的 cleanup task。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="idle")
        managed.idle_since = time.monotonic()
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_idle_ttl", new_callable=AsyncMock, return_value=9999):
            mgr._schedule_idle_cleanup("s1")
            first_task = managed._idle_cleanup_task
            mgr._schedule_idle_cleanup("s1")
            second_task = managed._idle_cleanup_task

        assert first_task is not second_task
        # Give the event loop a tick to process the cancellation
        await asyncio.sleep(0)
        assert first_task.cancelled()
        # cleanup
        second_task.cancel()

    async def test_finalize_turn_idle_schedules_cleanup(self, tmp_path):
        """_finalize_turn 产生 idle 状态时应调度 idle cleanup。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="running")
        mgr.sessions["s1"] = managed

        result_msg = {"type": "result", "subtype": "success", "is_error": False, "session_status": "idle"}

        with patch.object(mgr, "_schedule_idle_cleanup") as mock_schedule:
            with patch.object(mgr.meta_store, "update_status", new_callable=AsyncMock):
                await mgr._finalize_turn(managed, result_msg)

        mock_schedule.assert_called_once_with("s1")
        assert managed.idle_since is not None


class TestEnsureCapacity:
    async def test_under_limit_no_eviction(self, tmp_path):
        """活跃数低于上限时不淘汰。"""
        mgr = _make_manager(tmp_path)
        mgr.sessions["s1"] = _make_managed("s1")

        with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=5):
            await mgr._ensure_capacity()

        assert "s1" in mgr.sessions

    async def test_evicts_oldest_idle(self, tmp_path):
        """超限时淘汰最久 idle 的会话。"""
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
                mock_disc.assert_called_once_with("s_old")

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
    async def test_patrol_cleans_expired_idle(self, tmp_path):
        """巡检应清理超过 TTL 的 idle 会话。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="idle")
        managed.idle_since = time.monotonic() - 1000
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_idle_ttl", new_callable=AsyncMock, return_value=60):
            with patch.object(mgr, "_disconnect_session", new_callable=AsyncMock) as mock_disc:
                await mgr._patrol_once()
                mock_disc.assert_called_once_with("s1")

    async def test_patrol_skips_running(self, tmp_path):
        """巡检不应清理 running 会话。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="running")
        managed.idle_since = None
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_idle_ttl", new_callable=AsyncMock, return_value=60):
            with patch.object(mgr, "_disconnect_session", new_callable=AsyncMock) as mock_disc:
                await mgr._patrol_once()
                mock_disc.assert_not_called()

    async def test_patrol_skips_recent_idle(self, tmp_path):
        """巡检不应清理近期的 idle 会话。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="idle")
        managed.idle_since = time.monotonic()
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_idle_ttl", new_callable=AsyncMock, return_value=600):
            with patch.object(mgr, "_disconnect_session", new_callable=AsyncMock) as mock_disc:
                await mgr._patrol_once()
                mock_disc.assert_not_called()
