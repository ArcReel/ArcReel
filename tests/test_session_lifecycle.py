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
