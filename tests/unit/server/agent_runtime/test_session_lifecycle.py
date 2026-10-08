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
        """关闭未登记的会话是空操作：不抛错，也不动别的会话。"""
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
            with (
                patch.object(mgr, "_schedule_cleanup") as mock_schedule,
                patch.object(mgr.meta_store, "update_status", new_callable=AsyncMock),
            ):
                await mgr._finalize_turn(managed, result_msg)

            mock_schedule.assert_called_once_with("s1")
            assert managed.status == "completed"
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

    async def test_skips_idle_session_whose_autonomous_turn_is_not_settled(self, tmp_path):
        """自主轮次已读出、inbox 还没切 running：status 仍是 idle，也不能断开它。"""
        mgr = _make_manager(tmp_path)
        busy, busy_client = _make_managed("s_busy", status="idle")
        await _start(busy)
        busy.last_activity = time.monotonic() - 100
        busy.note_frame_read({"type": "assistant", "content": [], "parent_tool_use_id": None})
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
        """无 idle 会话时，应淘汰 completed/error/interrupted 状态的会话。"""
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
        """所有会话都在 running 时应抛出 SessionCapacityError。"""
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
        """错误消息中应包含当前 running 会话数。"""
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
        """巡检应清理超时的非 running 会话。"""
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
        """巡检不应清理 running 会话。"""
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
        """巡检不应清理近期活跃的会话。"""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1", status="completed")
        await _start(managed)
        managed.last_activity = time.monotonic()  # 刚刚活跃
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=600):
                await mgr._patrol_once()

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")


# --- 后台子智能体在途：断开 CLI 会连带杀掉它，完成后的自主轮次也不会发生 ------------


def _task_started(task_id: str = "t1", task_type: str = "local_agent") -> dict:
    return {"type": "system", "subtype": "task_started", "task_id": task_id, "task_type": task_type}


def _session_state(state: str) -> dict:
    """CLI 的 session_state_changed 帧，序列化后的形状（状态在 data 里）。"""
    data = {"type": "system", "subtype": "session_state_changed", "state": state}
    return {"type": "system", "subtype": "session_state_changed", "data": data}


async def _feed(mgr: SessionManager, managed: ManagedSession, *frames: dict) -> None:
    """经真实读取回调与 inbox 处理喂入消息帧，处理完即返回。"""
    managed.resolved_sdk_id = managed.session_id
    on_message = mgr._make_actor_message_callback([managed])
    for frame in frames:
        on_message(frame)
    managed._inbox.put_nowait(None)
    await mgr._process_inbox(managed)


class TestBackgroundWork:
    async def test_idle_cleanup_spares_session_with_background_agent(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed
        await _feed(mgr, managed, _task_started())

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=0):
                await mgr._cleanup_idle("s1")

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")

    async def test_idle_cleanup_evicts_session_with_only_a_background_shell(self, tmp_path):
        """后台 shell 可能永不结束（dev server、tail -f），与 SDK 同口径不算在途工作。"""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed
        await _feed(mgr, managed, _task_started(task_type="local_bash"))

        with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=0):
            await mgr._cleanup_idle("s1")

        assert "s1" not in mgr.sessions
        assert client.disconnected is True

    @pytest.mark.parametrize(
        "terminal_frame",
        [
            {"type": "system", "subtype": "task_notification", "task_id": "t1", "status": "completed"},
            # task_updated 序列化后不带 type，只能按 subtype 认
            {"subtype": "task_updated", "task_id": "t1", "status": "killed", "patch": {"status": "killed"}},
        ],
        ids=["task-notification", "task-updated-terminal"],
    )
    async def test_last_background_agent_finishing_restarts_idle_cleanup(self, tmp_path, terminal_frame):
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed

        actor_task = managed.actor.task
        assert actor_task is not None
        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=0):
                await _feed(mgr, managed, _task_started(), terminal_frame)
                # 清理断开 CLI 即 actor 退出；shield 让超时只报失败，不顺带取消 actor
                await asyncio.wait_for(asyncio.shield(actor_task), timeout=1.0)

            assert client.disconnected is True
        finally:
            await mgr.close_session("s1")

    async def test_capacity_rejects_rather_than_evicting_session_with_background_agent(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed
        await _feed(mgr, managed, _task_started())

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

    async def test_cli_exit_releases_background_agent_protection(self, tmp_path):
        """CLI 退出时后台子智能体随之终止，终态帧不会再来：会话不能永久占着名额。"""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        actor_task = managed.actor.task
        assert actor_task is not None
        actor_task.add_done_callback(mgr._make_actor_done_callback(managed))
        mgr.sessions["s1"] = managed
        managed.resolved_sdk_id = "s1"
        on_message = mgr._make_actor_message_callback([managed])
        on_message(_task_started())
        on_message(_session_state("running"))

        client.close_stream()
        await asyncio.wait_for(mgr._process_inbox(managed), timeout=1.0)

        with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=0):
            await mgr._cleanup_idle("s1")
        assert "s1" not in mgr.sessions

    async def test_capacity_evicts_unprotected_session_before_older_protected_one(self, tmp_path):
        mgr = _make_manager(tmp_path)
        protected, protected_client = _make_managed("s_bg")
        await _start(protected)
        mgr.sessions["s_bg"] = protected
        await _feed(mgr, protected, _task_started())
        protected.last_activity = time.monotonic() - 100
        plain, plain_client = _make_managed("s_plain")
        await _start(plain)
        mgr.sessions["s_plain"] = plain

        try:
            with patch.object(mgr, "_get_max_concurrent", new_callable=AsyncMock, return_value=2):
                await mgr._ensure_capacity()

            assert plain_client.disconnected is True
            assert protected_client.disconnected is False
        finally:
            await mgr.close_session("s_bg")
            await mgr.close_session("s_plain")

    async def test_patrol_spares_stale_session_with_background_agent(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1", status="completed")
        await _start(managed)
        mgr.sessions["s1"] = managed
        await _feed(mgr, managed, _task_started())
        managed.last_activity = time.monotonic() - 1000

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=60):
                await mgr._patrol_once()

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")


# --- CLI 报告的会话状态：子智能体在一轮的 result 之前结束时，账本已清空，CLI 仍欠一轮 ------


class TestCliSessionState:
    @pytest.mark.parametrize("state", ["running", "requires_action"])
    async def test_idle_cleanup_spares_session_while_cli_still_reports_work(self, tmp_path, state):
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed
        finished = {"type": "system", "subtype": "task_notification", "task_id": "t1", "status": "completed"}

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=0):
                await _feed(mgr, managed, _task_started(), _session_state(state), finished)
                await mgr._cleanup_idle("s1")

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")

    async def test_cli_reporting_idle_restarts_idle_cleanup(self, tmp_path):
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed

        actor_task = managed.actor.task
        assert actor_task is not None
        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=0):
                await _feed(mgr, managed, _session_state("running"), _session_state("idle"))
                await asyncio.wait_for(asyncio.shield(actor_task), timeout=1.0)

            assert client.disconnected is True
        finally:
            await mgr.close_session("s1")

    async def test_cli_idle_does_not_release_a_live_background_agent(self, tmp_path):
        """与 SDK 同口径取并集：CLI 可配置为每轮结束都报 idle，此时只有账本知道子智能体还在。"""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1")
        await _start(managed)
        mgr.sessions["s1"] = managed

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=0):
                await _feed(mgr, managed, _task_started(), _session_state("idle"))
                await mgr._cleanup_idle("s1")

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")

    async def test_patrol_counts_idle_time_from_when_background_work_ended(self, tmp_path):
        """子智能体跑得比两倍清理延迟还久：结束时不能按它开始前的活跃时间被巡检立即驱逐。"""
        mgr = _make_manager(tmp_path)
        managed, client = _make_managed("s1", status="completed")
        await _start(managed)
        mgr.sessions["s1"] = managed
        finished = {"type": "system", "subtype": "task_notification", "task_id": "t1", "status": "completed"}

        try:
            with patch.object(mgr, "_get_cleanup_delay", new_callable=AsyncMock, return_value=60):
                await _feed(mgr, managed, _task_started())
                managed.last_activity = time.monotonic() - 1000
                await _feed(mgr, managed, finished)
                await mgr._patrol_once()

            assert "s1" in mgr.sessions
            assert client.disconnected is False
        finally:
            await mgr.close_session("s1")
