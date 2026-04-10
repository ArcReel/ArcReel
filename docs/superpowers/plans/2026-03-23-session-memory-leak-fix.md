# Claude Subprocess Memory Leak Fix Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Claude SDK subprocess memory leak; implement three defensive layers: automatic cleanup of idle sessions, concurrency limit, and periodic inspection.

**Architecture:** 在 `SessionManager` 中新增 `_disconnect_session()` 统一清理方法、`_schedule_cleanup()` TTL 定时器、`_ensure_capacity()` 并发控制、`_patrol_loop()` 安全网巡检。配置通过 `SystemSetting` K-V 表存储，前端 `AgentConfigTab` 新增折叠式高级设置面板。

**Tech Stack:** Python asyncio / FastAPI / SQLAlchemy async / React 19 / TypeScript / Tailwind CSS

**Spec:** `docs/superpowers/specs/2026-03-23-session-memory-leak-fix-design.md`

---

## Chunk 1: 后端核心 — SessionManager 生命周期管理

### Task 1: ManagedSession 新增字段 + FakeSDKClient 扩展

**Files:**
- Modify: `server/agent_runtime/session_manager.py:59-73` (ManagedSession dataclass)
- Modify: `tests/fakes.py:10-42` (FakeSDKClient)

- [ ] **Step 1: 在 ManagedSession 中新增 3 个字段**

```python
# server/agent_runtime/session_manager.py — ManagedSession dataclass, 在 interrupt_requested 之后添加
    idle_since: Optional[float] = None                            # monotonic timestamp when entering idle
    last_activity: Optional[float] = None                         # updated on every send/receive
    _cleanup_task: Optional[asyncio.Task] = None             # current idle cleanup timer
```

同时At the top of the file `import` 区添加 `import time`（如果尚未导入）。

- [ ] **Step 2: 给 FakeSDKClient 添加 disconnect 和 connected 追踪**

```python
# tests/fakes.py — FakeSDKClient 中新增
    def __init__(self, messages=None):
        self._messages = list(messages) if messages else []
        self.sent_queries: list[str] = []
        self.interrupted = False
        self.disconnected = False          # 新增

    async def disconnect(self) -> None:    # 新增
        self.disconnected = True

    async def connect(self) -> None:       # 新增
        self.disconnected = False
```

- [ ] **Step 3: 运行现有测试confirm no regressions**

Run: `uv run python -m pytest tests/test_session_manager_more.py tests/test_session_manager_sdk_session_id.py tests/test_session_manager_user_input.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/fakes.py
git commit -m "feat: ManagedSession 新增 idle_since/last_activity/_cleanup_task 字段"
```

---

### Task 2: SessionCapacityError 自定义异常

**Files:**
- Modify: `server/agent_runtime/session_manager.py` (文件顶部，`_utc_now_iso` 之前)

- [ ] **Step 1: 添加自定义异常类**

在 `session_manager.py` 的 `_utc_now_iso` 函数之前添加：

```python
class SessionCapacityError(Exception):
    """所有并发槽位已被 running 会话占满，无法创建新连接。"""
    pass
```

- [ ] **Step 2: Commit**

```bash
git add server/agent_runtime/session_manager.py
git commit -m "feat: 添加 SessionCapacityError 自定义异常"
```

---

### Task 3: `_disconnect_session()` 统一清理方法

**Files:**
- Modify: `server/agent_runtime/session_manager.py` (`SessionManager` 类内，`_schedule_cleanup` 方法之后)
- Create: `tests/test_session_lifecycle.py`

- [ ] **Step 1: 编写 `_disconnect_session` 的测试**

```python
# tests/test_session_lifecycle.py
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
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_session_lifecycle.py::TestDisconnectSession -v`
Expected: FAIL — `_disconnect_session` 不存在

- [ ] **Step 3: 实现 `_disconnect_session`**

在 `SessionManager` 类中，`_schedule_cleanup` 方法之后添加：

```python
    async def _disconnect_session(self, session_id: str) -> None:
        """安全断开并移除一个会话，处理 consumer_task 和 connect_lock。"""
        managed = self.sessions.get(session_id)
        if managed is None:
            return
        # 取消 idle cleanup 定时器
        if managed._cleanup_task and not managed._cleanup_task.done():
            managed._cleanup_task.cancel()
        # 取消 consumer_task 并等待完成，防止与 disconnect 竞争
        if managed.consumer_task and not managed.consumer_task.done():
            managed.consumer_task.cancel()
            await asyncio.gather(managed.consumer_task, return_exceptions=True)
        managed.clear_buffer()
        try:
            await managed.client.disconnect()
        except Exception:
            logger.debug("disconnect non-fatal error for %s", session_id, exc_info=True)
        self.sessions.pop(session_id, None)
        self._connect_locks.pop(session_id, None)
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_session_lifecycle.py::TestDisconnectSession -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_lifecycle.py
git commit -m "feat: 添加 _disconnect_session 统一清理方法"
```

---

### Task 4: 配置读取方法 `_get_idle_ttl` / `_get_max_concurrent`

**Files:**
- Modify: `server/agent_runtime/session_manager.py` (`SessionManager` 类内)
- Modify: `tests/test_session_lifecycle.py`

- [ ] **Step 1: 编写配置读取测试**

```python
# tests/test_session_lifecycle.py — 新增测试类
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
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_session_lifecycle.py::TestConfigReading -v`
Expected: FAIL

- [ ] **Step 3: 实现配置读取方法**

在 `SessionManager` 类中，`_disconnect_session` 之后添加：

```python
    async def _get_idle_ttl(self) -> int:
        """返回 idle TTL 秒数，默认 600（10 分钟）。"""
        try:
            from lib.db import async_session_factory
            from lib.config.service import ConfigService

            async with async_session_factory() as session:
                svc = ConfigService(session)
                val = await svc.get_setting("agent_session_idle_ttl_minutes", "10")
            return max(int(val), 1) * 60
        except Exception:
            logger.warning("读取 idle TTL 配置失败，使用默认值", exc_info=True)
            return 600

    async def _get_max_concurrent(self) -> int:
        """返回最大并发会话数，默认 5。"""
        try:
            from lib.db import async_session_factory
            from lib.config.service import ConfigService

            async with async_session_factory() as session:
                svc = ConfigService(session)
                val = await svc.get_setting("agent_max_concurrent_sessions", "5")
            return max(int(val), 1)
        except Exception:
            logger.warning("读取 max_concurrent 配置失败，使用默认值", exc_info=True)
            return 5
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_session_lifecycle.py::TestConfigReading -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_lifecycle.py
git commit -m "feat: 添加 _get_idle_ttl 和 _get_max_concurrent 配置读取"
```

---

### Task 5: `_schedule_cleanup()` — 层 1 Idle TTL

**Files:**
- Modify: `server/agent_runtime/session_manager.py:858-878` (`_finalize_turn`)
- Modify: `server/agent_runtime/session_manager.py` (新增 `_schedule_cleanup`)
- Modify: `tests/test_session_lifecycle.py`

- [ ] **Step 1: 编写 idle cleanup 测试**

```python
# tests/test_session_lifecycle.py — 新增测试类
class TestIdleCleanup:
    async def test_idle_cleanup_disconnects_after_ttl(self, tmp_path):
        """TTL 到期后 idle 会话应被清理。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="idle")
        managed.idle_since = time.monotonic() - 10  # 已 idle 10 秒
        mgr.sessions["s1"] = managed

        # 用极短 TTL 触发
        with patch.object(mgr, "_get_idle_ttl", return_value=1):  # 1 秒 TTL
            mgr._schedule_cleanup("s1")
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

        with patch.object(mgr, "_get_idle_ttl", return_value=1):
            mgr._schedule_cleanup("s1")
            # 模拟用户发送新消息：idle_since 被刷新
            managed.idle_since = time.monotonic() + 100  # 未来时间
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

        with patch.object(mgr, "_get_idle_ttl", return_value=9999):
            mgr._schedule_cleanup("s1")
            first_task = managed._cleanup_task
            mgr._schedule_cleanup("s1")
            second_task = managed._cleanup_task

        assert first_task is not second_task
        assert first_task.cancelled()
        # cleanup
        second_task.cancel()

    async def test_finalize_turn_idle_schedules_cleanup(self, tmp_path):
        """_finalize_turn 产生 idle 状态时应调度 idle cleanup。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="running")
        mgr.sessions["s1"] = managed

        result_msg = {"type": "result", "subtype": "success", "is_error": False}

        with patch.object(mgr, "_schedule_cleanup") as mock_schedule:
            with patch.object(mgr.meta_store, "update_status", new_callable=AsyncMock):
                await mgr._finalize_turn(managed, result_msg)

        mock_schedule.assert_called_once_with("s1")
        assert managed.idle_since is not None
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_session_lifecycle.py::TestIdleCleanup -v`
Expected: FAIL

- [ ] **Step 3: 实现 `_schedule_cleanup`**

在 `SessionManager` 类中，`_get_max_concurrent` 之后添加：

```python
    def _schedule_cleanup(self, session_id: str) -> None:
        """为 idle 会话调度延迟清理，到期后释放 SDK 子进程。"""
        managed = self.sessions.get(session_id)
        if managed is None:
            return
        # 取消旧的 cleanup task
        if managed._cleanup_task and not managed._cleanup_task.done():
            managed._cleanup_task.cancel()

        idle_since_snapshot = managed.idle_since

        async def _idle_cleanup() -> None:
            ttl = await self._get_idle_ttl()
            await asyncio.sleep(ttl)
            m = self.sessions.get(session_id)
            if m is None:
                return
            # 会话已恢复活跃或 idle_since 已刷新 → 跳过
            if m.status != "idle" or m.idle_since != idle_since_snapshot:
                return
            logger.info("Idle TTL 到期，清理会话 session_id=%s", session_id)
            await self._disconnect_session(session_id)

        managed._cleanup_task = asyncio.create_task(_idle_cleanup())
```

- [ ] **Step 4: 修改 `_finalize_turn` 以触发 idle cleanup**

在 `session_manager.py` 的 `_finalize_turn` 方法中，将 877-878 行：

```python
        if final_status not in ("idle", "running"):
            self._schedule_cleanup(managed.session_id)
```

改为：

```python
        if final_status == "idle":
            managed.idle_since = time.monotonic()
            self._schedule_cleanup(managed.session_id)
        elif final_status not in ("running",):
            self._schedule_cleanup(managed.session_id)
```

- [ ] **Step 5: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_session_lifecycle.py::TestIdleCleanup -v`
Expected: all PASS

- [ ] **Step 6: 运行全部 session_manager 测试confirm no regressions**

Run: `uv run python -m pytest tests/test_session_manager_more.py tests/test_session_manager_sdk_session_id.py tests/test_session_manager_user_input.py tests/test_session_lifecycle.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_lifecycle.py
git commit -m "feat: 实现层 1 Idle TTL 定时清理"
```

---

### Task 6: `_ensure_capacity()` — 层 2 并发上限 + LRU 淘汰

**Files:**
- Modify: `server/agent_runtime/session_manager.py` (新增 `_ensure_capacity`，修改 `send_new_session` 和 `get_or_connect`)
- Modify: `tests/test_session_lifecycle.py`

- [ ] **Step 1: 编写并发上限测试**

```python
# tests/test_session_lifecycle.py — 新增测试类
class TestEnsureCapacity:
    async def test_under_limit_no_eviction(self, tmp_path):
        """活跃数低于上限时不淘汰。"""
        mgr = _make_manager(tmp_path)
        mgr.sessions["s1"] = _make_managed("s1")

        with patch.object(mgr, "_get_max_concurrent", return_value=5):
            await mgr._ensure_capacity()  # should not raise

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

        with patch.object(mgr, "_get_max_concurrent", return_value=2):
            with patch.object(mgr, "_disconnect_session", new_callable=AsyncMock) as mock_disc:
                await mgr._ensure_capacity()
                mock_disc.assert_called_once_with("s_old")

    async def test_all_running_raises_capacity_error(self, tmp_path):
        """所有会话都在 running 时应抛出 SessionCapacityError。"""
        mgr = _make_manager(tmp_path)
        for i in range(3):
            mgr.sessions[f"s{i}"] = _make_managed(f"s{i}", status="running")

        with patch.object(mgr, "_get_max_concurrent", return_value=3):
            with pytest.raises(SessionCapacityError, match="正在进行的会话"):
                await mgr._ensure_capacity()

    async def test_capacity_error_message_includes_count(self, tmp_path):
        """错误消息中应包含当前 running 会话数。"""
        mgr = _make_manager(tmp_path)
        for i in range(3):
            mgr.sessions[f"s{i}"] = _make_managed(f"s{i}", status="running")

        with patch.object(mgr, "_get_max_concurrent", return_value=3):
            with pytest.raises(SessionCapacityError, match="3个"):
                await mgr._ensure_capacity()
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_session_lifecycle.py::TestEnsureCapacity -v`
Expected: FAIL

- [ ] **Step 3: 实现 `_ensure_capacity`**

在 `SessionManager` 类中，`_schedule_cleanup` 之后添加：

```python
    async def _ensure_capacity(self) -> None:
        """确保有空余并发槽位，必要时淘汰最久未活跃的非 running 会话。"""
        max_concurrent = await self._get_max_concurrent()
        active = [s for s in self.sessions.values() if s.client is not None]

        if len(active) < max_concurrent:
            return

        # 可淘汰的会话：非 running 状态（idle / completed / error / interrupted）
        evictable = sorted(
            [s for s in active if s.status != "running"],
            key=lambda s: s.last_activity or 0,
        )

        if evictable:
            victim = evictable[0]
            logger.info(
                "并发上限，淘汰 session_id=%s (status=%s)",
                victim.session_id,
                victim.status,
            )
            await self._disconnect_session(victim.session_id)
            return

        # 所有会话都在 running → 拒绝
        raise SessionCapacityError(
            f"当前有{len(active)}个正在进行的会话，已达到最大上限，请稍后重试"
        )
```

- [ ] **Step 4: 在 `send_new_session` 中调用 `_ensure_capacity`**

在 `session_manager.py` 的 `send_new_session` 方法中，`temp_id = uuid4().hex` 之前（约 622 行）插入：

```python
        await self._ensure_capacity()
```

- [ ] **Step 5: 在 `get_or_connect` 中调用 `_ensure_capacity`**

在 `session_manager.py` 的 `get_or_connect` 方法中，`options = self._build_options(...)` 之前（约 708 行），lock 内部插入：

```python
            await self._ensure_capacity()
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_session_lifecycle.py::TestEnsureCapacity -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_lifecycle.py
git commit -m "feat: 实现层 2 并发上限 + LRU 淘汰"
```

---

### Task 7: `_patrol_loop()` — 层 3 定期巡检

**Files:**
- Modify: `server/agent_runtime/session_manager.py` (新增 `_patrol_loop`、`start_patrol`、修改 `shutdown_gracefully`)
- Modify: `tests/test_session_lifecycle.py`

- [ ] **Step 1: 编写巡检测试**

```python
# tests/test_session_lifecycle.py — 新增测试类
class TestPatrolLoop:
    async def test_patrol_cleans_expired_idle(self, tmp_path):
        """巡检应清理超过 TTL 的 idle 会话。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="idle")
        managed.idle_since = time.monotonic() - 1000  # 很久之前
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_idle_ttl", return_value=60):
            with patch.object(mgr, "_disconnect_session", new_callable=AsyncMock) as mock_disc:
                # 直接调用一次巡检逻辑
                await mgr._patrol_once()
                mock_disc.assert_called_once_with("s1")

    async def test_patrol_skips_running(self, tmp_path):
        """巡检不应清理 running 会话。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="running")
        managed.idle_since = None
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_idle_ttl", return_value=60):
            with patch.object(mgr, "_disconnect_session", new_callable=AsyncMock) as mock_disc:
                await mgr._patrol_once()
                mock_disc.assert_not_called()

    async def test_patrol_skips_recent_idle(self, tmp_path):
        """巡检不应清理近期的 idle 会话。"""
        mgr = _make_manager(tmp_path)
        managed = _make_managed("s1", status="idle")
        managed.idle_since = time.monotonic()  # 刚进入 idle
        mgr.sessions["s1"] = managed

        with patch.object(mgr, "_get_idle_ttl", return_value=600):
            with patch.object(mgr, "_disconnect_session", new_callable=AsyncMock) as mock_disc:
                await mgr._patrol_once()
                mock_disc.assert_not_called()
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_session_lifecycle.py::TestPatrolLoop -v`
Expected: FAIL

- [ ] **Step 3: 实现巡检**

在 `SessionManager` 类中添加：

```python
    _PATROL_INTERVAL = 300  # 5 分钟

    async def _patrol_once(self) -> None:
        """单次巡检：清理所有超时的 idle 会话。"""
        ttl = await self._get_idle_ttl()
        now = time.monotonic()
        for sid, managed in list(self.sessions.items()):
            if managed.status == "idle" and managed.idle_since:
                if now - managed.idle_since > ttl:
                    logger.info("巡检清理超时 idle 会话 session_id=%s", sid)
                    await self._disconnect_session(sid)

    async def _patrol_loop(self) -> None:
        """后台定期巡检循环。"""
        while True:
            await asyncio.sleep(self._PATROL_INTERVAL)
            try:
                await self._patrol_once()
            except Exception:
                logger.warning("巡检循环异常", exc_info=True)

    def start_patrol(self) -> None:
        """启动巡检后台任务（应在应用 startup 时调用）。"""
        self._patrol_task = asyncio.create_task(self._patrol_loop())
```

- [ ] **Step 4: 修改 `shutdown_gracefully` 取消巡检任务**

在 `shutdown_gracefully` 方法开头添加：

```python
        # 取消巡检任务
        patrol = getattr(self, "_patrol_task", None)
        if patrol and not patrol.done():
            patrol.cancel()
```

- [ ] **Step 5: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_session_lifecycle.py::TestPatrolLoop -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_lifecycle.py
git commit -m "feat: 实现层 3 定期巡检安全网"
```

---

### Task 8: `last_activity` 更新 + 巡检启动集成

**Files:**
- Modify: `server/agent_runtime/session_manager.py` (`send_message`, `send_new_session` 中更新 `last_activity`)
- Modify: `server/app.py` (startup 中启动巡检)

- [ ] **Step 1: 在 `send_new_session` 中设置 `last_activity`**

在 `send_new_session` 方法中，创建 `ManagedSession` 后（约 638 行）添加：

```python
        managed.last_activity = time.monotonic()
```

- [ ] **Step 2: 在 `send_message` 中更新 `last_activity`**

在 `send_message` 方法中，`managed = await self.get_or_connect(...)` 之后添加：

```python
        managed.last_activity = time.monotonic()
        # 取消待执行的 idle cleanup（会话恢复活跃）
        if managed._cleanup_task and not managed._cleanup_task.done():
            managed._cleanup_task.cancel()
            managed._cleanup_task = None
        managed.idle_since = None
```

- [ ] **Step 3: 在 app startup 中启动巡检**

在 `server/app.py` 的 `lifespan` 函数中，`await assistant.assistant_service.startup()` 之后添加：

```python
    assistant.assistant_service.session_manager.start_patrol()
```

- [ ] **Step 4: 运行全部生命周期测试**

Run: `uv run python -m pytest tests/test_session_lifecycle.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/agent_runtime/session_manager.py server/app.py
git commit -m "feat: last_activity 追踪 + app startup 启动巡检"
```

---

### Task 9: 路由层捕获 SessionCapacityError → 503

**Files:**
- Modify: `server/routers/assistant.py:64-87` (`send_message` 端点)
- Modify: `server/routers/agent_chat.py:125-195` (`agent_chat` 端点)

- [ ] **Step 1: 在 `assistant.py` 中添加 503 处理**

在 `server/routers/assistant.py` 的 `send_message` 端点中，import 区添加：

```python
from server.agent_runtime.session_manager import SessionCapacityError
```

在 `send_message` 函数的 try/except 块中，`except FileNotFoundError` 之前添加：

```python
    except SessionCapacityError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
```

- [ ] **Step 2: 在 `agent_chat.py` 中添加 503 处理**

在 `server/routers/agent_chat.py` 中，import 区添加：

```python
from server.agent_runtime.session_manager import SessionCapacityError
```

在 `agent_chat` 函数的 `service.send_or_create(...)` 调用的 try/except 块中，`except TimeoutError` 之前添加：

```python
    except SessionCapacityError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
```

- [ ] **Step 3: 运行现有路由测试（如果有）confirm no regressions**

Run: `uv run python -m pytest tests/ -k "agent_chat or assistant" -v --no-header 2>&1 | head -30`

- [ ] **Step 4: Commit**

```bash
git add server/routers/assistant.py server/routers/agent_chat.py
git commit -m "feat: 路由层捕获 SessionCapacityError 返回 503"
```

---

## Chunk 2: 配置 API + 前端 UI

### Task 10: 后端配置 API 扩展

**Files:**
- Modify: `server/routers/system_config.py:88-99` (SystemConfigPatchRequest)
- Modify: `server/routers/system_config.py:117-146` (GET)
- Modify: `server/routers/system_config.py:154-209` (PATCH)

- [ ] **Step 1: 扩展 Pydantic 模型**

在 `SystemConfigPatchRequest` 中添加两个字段（`claude_code_subagent_model` 之后）：

```python
    agent_session_idle_ttl_minutes: Optional[int] = None
    agent_max_concurrent_sessions: Optional[int] = None
```

- [ ] **Step 2: 在 GET 响应中返回新字段**

在 `get_system_config` 函数的 `settings` dict 中，`claude_code_subagent_model` 行之后添加：

```python
        "agent_session_idle_ttl_minutes": int(all_s.get("agent_session_idle_ttl_minutes") or "10"),
        "agent_max_concurrent_sessions": int(all_s.get("agent_max_concurrent_sessions") or "5"),
```

- [ ] **Step 3: 在 PATCH 处理中添加整数设置处理**

在 `patch_system_config` 函数中，`# String settings` 循环之前添加：

```python
    # Integer settings with range validation
    _INT_SETTINGS_RANGES = {
        "agent_session_idle_ttl_minutes": (1, 60),
        "agent_max_concurrent_sessions": (1, 20),
    }
    for key, (min_val, max_val) in _INT_SETTINGS_RANGES.items():
        if key in patch and patch[key] is not None:
            value = int(patch[key])
            if not (min_val <= value <= max_val):
                raise HTTPException(
                    status_code=422,
                    detail=f"{key} 应在 {min_val}-{max_val} 之间",
                )
            await svc.set_setting(key, str(value))
```

- [ ] **Step 4: 运行全部测试confirm no regressions**

Run: `uv run python -m pytest tests/ -v --no-header 2>&1 | tail -10`
Expected: 无新失败

- [ ] **Step 5: Commit**

```bash
git add server/routers/system_config.py
git commit -m "feat: 系统配置 API 新增会话管理设置"
```

---

### Task 11: 前端类型定义扩展

**Files:**
- Modify: `frontend/src/types/system.ts`

- [ ] **Step 1: 在 `SystemConfigSettings` 中新增字段**

在 `claude_code_subagent_model: string;` 之后添加：

```typescript
  agent_session_idle_ttl_minutes: number;
  agent_max_concurrent_sessions: number;
```

- [ ] **Step 2: 在 `SystemConfigPatch` 中新增字段**

在 `claude_code_subagent_model?: string;` 之后添加：

```typescript
  agent_session_idle_ttl_minutes?: number;
  agent_max_concurrent_sessions?: number;
```

- [ ] **Step 3: TypeScript 类型检查**

Run: `cd frontend && pnpm typecheck`
Expected: PASS（或仅有已存在的错误）

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/system.ts
git commit -m "feat: 前端类型新增会话管理配置字段"
```

---

### Task 12: AgentConfigTab 高级设置 UI

**Files:**
- Modify: `frontend/src/components/pages/AgentConfigTab.tsx`

- [ ] **Step 1: 扩展 AgentDraft 接口**

在 `AgentDraft` 中，`subagentModel: string;` 之后添加：

```typescript
  sessionIdleTtlMinutes: string;
  maxConcurrentSessions: string;
```

- [ ] **Step 2: 更新 `buildDraft`**

在 `buildDraft` 函数的 return 对象中添加：

```typescript
    sessionIdleTtlMinutes: String(s.agent_session_idle_ttl_minutes ?? 10),
    maxConcurrentSessions: String(s.agent_max_concurrent_sessions ?? 5),
```

- [ ] **Step 3: 更新 `deepEqual`**

在 `deepEqual` 的比较链中添加：

```typescript
    a.sessionIdleTtlMinutes === b.sessionIdleTtlMinutes &&
    a.maxConcurrentSessions === b.maxConcurrentSessions
```

- [ ] **Step 4: 更新 `buildPatch`**

在 `buildPatch` 函数中添加：

```typescript
  if (draft.sessionIdleTtlMinutes !== saved.sessionIdleTtlMinutes)
    patch.agent_session_idle_ttl_minutes = Number(draft.sessionIdleTtlMinutes) || 10;
  if (draft.maxConcurrentSessions !== saved.maxConcurrentSessions)
    patch.agent_max_concurrent_sessions = Number(draft.maxConcurrentSessions) || 5;
```

- [ ] **Step 5: 更新初始 state 和 savedRef**

在 `AgentConfigTab` 组件的 `useState` 和 `useRef` 初始值中添加：

```typescript
    sessionIdleTtlMinutes: "10",
    maxConcurrentSessions: "5",
```

- [ ] **Step 6: 添加高级设置折叠面板 UI**

在 JSX 中，模型路由 `</details>` 所在的 `</div></div>` 之后（约 569 行），`<TabSaveFooter` 之前，添加：

```tsx
      {/* 高级设置 */}
      <div className={cardClassName}>
        <details>
          <summary className="flex cursor-pointer select-none items-center gap-2 text-sm font-medium text-gray-400 transition-colors hover:text-gray-200">
            <SlidersHorizontal className="h-4 w-4" />
            高级设置
          </summary>
          <div className="mt-4 space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-200">
                会话空闲超时（分钟）
              </label>
              <p className="mt-0.5 text-xs text-gray-500">
                会话空闲超过此时间后自动释放资源，再次对话时会自动恢复
              </p>
              <input
                type="number"
                min={1}
                max={60}
                value={draft.sessionIdleTtlMinutes}
                onChange={(e) => updateDraft("sessionIdleTtlMinutes", e.target.value)}
                className={`${inputClassName} mt-1.5 max-w-[120px]`}
                disabled={saving}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-200">
                最大并发会话数
              </label>
              <p className="mt-0.5 text-xs text-gray-500">
                同时保持活跃的智能体会话上限，超出时自动释放最久未使用的会话（清理的会话会持久化，下次对话时恢复）
              </p>
              <input
                type="number"
                min={1}
                max={20}
                value={draft.maxConcurrentSessions}
                onChange={(e) => updateDraft("maxConcurrentSessions", e.target.value)}
                className={`${inputClassName} mt-1.5 max-w-[120px]`}
                disabled={saving}
              />
            </div>
          </div>
        </details>
      </div>
```

- [ ] **Step 7: TypeScript 类型检查**

Run: `cd frontend && pnpm typecheck`
Expected: PASS

- [ ] **Step 8: 前端构建验证**

Run: `cd frontend && pnpm build`
Expected: 构建成功

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/pages/AgentConfigTab.tsx
git commit -m "feat: 智能体配置页新增高级设置折叠面板"
```

---

### Task 13: 全量验证

**Files:** 无新增修改

- [ ] **Step 1: 运行全部后端测试**

Run: `uv run python -m pytest -v`
Expected: all PASS

- [ ] **Step 2: 运行前端检查**

Run: `cd frontend && pnpm check`
Expected: typecheck + test 全部 PASS

- [ ] **Step 3: 最终 Commit（如有遗留修复）**

若有修复，提交后完成。
