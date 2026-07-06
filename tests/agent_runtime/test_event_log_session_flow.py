"""FakeSDKClient 驱动会话：事件日志端到端产出（验收：seq 单调、类型正确）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from server.agent_runtime.event_log import EventLogStore, build_user_entry
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore
from tests.fakes import FakeSDKClient

SDK_ID = "sdk-e2e-1"


@pytest.fixture()
async def db_factory(tmp_path):
    """文件 SQLite + NullPool：本测试的写入（inbox 任务）与轮询读并发，
    内存库 StaticPool 共享单连接会让事务交错互相破坏。"""
    from sqlalchemy import event, pool

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'flow.db'}",
        poolclass=pool.NullPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture()
async def manager(tmp_path, db_factory):
    return SessionManager(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        meta_store=SessionMetaStore(session_factory=db_factory),
        event_log_store=EventLogStore(session_factory=db_factory),
    )


def _new_session_messages() -> list[dict]:
    """一轮完整对话：init → 用户回放 → 流式 → assistant(工具) → tool_result → subagent → result。"""
    return [
        {"type": "system", "subtype": "init", "session_id": SDK_ID, "uuid": "init-1"},
        # SDK 回放的用户消息（POST 受理时已写日志，须被跳过）
        {"type": "user", "content": "帮我写分镜", "uuid": "sdk-u1", "session_id": SDK_ID},
        {
            "type": "stream_event",
            "session_id": SDK_ID,
            "uuid": "se-1",
            "event": {"type": "message_start", "message": {"id": "msg_01"}},
        },
        {
            "type": "stream_event",
            "session_id": SDK_ID,
            "uuid": "se-2",
            "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "好的"}},
        },
        {
            "type": "assistant",
            "message_id": "msg_01",
            "uuid": "a-1",
            "session_id": SDK_ID,
            "content": [
                {"type": "text", "text": "好的"},
                {"type": "tool_use", "id": "tu-1", "name": "Bash", "input": {"command": "ls"}},
            ],
        },
        {
            "type": "user",
            "uuid": "u-tr-1",
            "session_id": SDK_ID,
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "file.txt", "is_error": False}],
        },
        {
            "type": "assistant",
            "message_id": "msg_02",
            "uuid": "a-sub",
            "session_id": SDK_ID,
            "parent_tool_use_id": "tu-1",
            "content": [{"type": "text", "text": "子任务输出"}],
        },
        {"type": "result", "subtype": "success", "is_error": False, "session_id": SDK_ID, "uuid": "r-1"},
    ]


async def _wait_for_entries(store: EventLogStore, session_id: str, count: int, timeout: float = 5.0) -> list[dict]:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        entries = await store.list_after(session_id)
        if len(entries) >= count:
            return entries
        if asyncio.get_running_loop().time() >= deadline:
            return entries
        await asyncio.sleep(0.02)


class TestNewSessionEventLogFlow:
    async def test_full_round_produces_typed_monotonic_entries(self, manager: SessionManager):
        client = FakeSDKClient(messages=_new_session_messages())
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch("server.agent_runtime.session_manager.tag_session", None),
        ):
            user_entry = build_user_entry([{"type": "text", "text": "帮我写分镜"}])
            sdk_id = await manager.send_new_session(
                "demo",
                "帮我写分镜",
                user_entry=user_entry,
                client_key="ck-new-1",
            )

        assert sdk_id == SDK_ID
        # POST 受理响应的权威条目：seq 0、身份已分配
        managed = manager.sessions[SDK_ID]
        assert managed.initial_user_log_entry is not None
        assert managed.initial_user_log_entry["seq"] == 0
        assert managed.initial_user_log_entry["type"] == "user"

        entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 4)
        try:
            assert [e["seq"] for e in entries] == [0, 1, 2, 3]
            assert [e["type"] for e in entries] == ["user", "assistant", "tool_result", "assistant"]

            # 用户条目（受理写入，SDK 回放副本未产生重复）
            assert entries[0]["content"] == [{"type": "text", "text": "帮我写分镜"}]

            # assistant 条目携带 message_id（draft 精确替换身份）
            assert entries[1]["message_id"] == "msg_01"
            assert entries[1]["content"][1]["type"] == "tool_use"

            # tool_result 是独立条目且引用 tool_use_id，不伪装成 user 消息
            assert entries[2]["tool_use_id"] == "tu-1"
            assert entries[2]["content"] == "file.txt"
            assert entries[2]["is_error"] is False

            # subagent 条目带 parent_tool_use_id 收录
            assert entries[3]["parent_tool_use_id"] == "tu-1"

            # 一轮结束后 draft 已随 result 丢弃
            assert manager.get_draft_state(SDK_ID)["draft"] is None
        finally:
            await manager.close_session(SDK_ID)

    async def test_send_message_writes_user_entry_before_query(self, manager: SessionManager, db_factory):
        meta = await manager.meta_store.create("demo", SDK_ID)
        client = FakeSDKClient(messages=[{"type": "result", "subtype": "success", "session_id": SDK_ID, "uuid": "r-1"}])
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch("server.agent_runtime.session_manager.tag_session", None),
        ):
            user_entry = build_user_entry([{"type": "text", "text": "继续"}])
            log_entry = await manager.send_message(
                SDK_ID,
                "继续",
                meta=meta,
                user_entry=user_entry,
                client_key="ck-2",
            )

        try:
            assert log_entry is not None
            assert log_entry["seq"] == 0
            assert client.sent_queries == ["继续"]

            # 同一幂等键重试：返回既有条目，不重复送 SDK
            retry_entry = build_user_entry([{"type": "text", "text": "继续"}])
            managed = manager.sessions[SDK_ID]
            managed.status = "idle"  # 模拟上一轮已结束
            second = await manager.send_message(
                SDK_ID,
                "继续",
                meta=meta,
                user_entry=retry_entry,
                client_key="ck-2",
            )
            assert second is not None
            assert second["seq"] == log_entry["seq"]
            assert second["uuid"] == log_entry["uuid"]
            assert client.sent_queries == ["继续"]  # 未重复投递
            entries = await manager.event_log_store.list_after(SDK_ID)
            assert len(entries) == 1
        finally:
            await manager.close_session(SDK_ID)

    async def test_retry_while_running_returns_idempotent_success(self, manager: SessionManager):
        """受理成功但响应丢失、轮次仍在运行时，同幂等键重试得到条目而非 400。"""
        meta = await manager.meta_store.create("demo", SDK_ID)
        client = FakeSDKClient(messages=[{"type": "result", "subtype": "success", "session_id": SDK_ID, "uuid": "r-1"}])
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch("server.agent_runtime.session_manager.tag_session", None),
        ):
            first = await manager.send_message(
                SDK_ID,
                "继续",
                meta=meta,
                user_entry=build_user_entry([{"type": "text", "text": "继续"}]),
                client_key="ck-run",
            )
            try:
                assert first is not None
                manager.sessions[SDK_ID].status = "running"  # 模拟轮次仍在执行

                retry = await manager.send_message(
                    SDK_ID,
                    "继续",
                    meta=meta,
                    user_entry=build_user_entry([{"type": "text", "text": "继续"}]),
                    client_key="ck-run",
                )
                assert retry is not None
                assert retry["seq"] == first["seq"]
                assert client.sent_queries == ["继续"]  # 未重复投递
            finally:
                await manager.close_session(SDK_ID)

    async def test_send_query_failure_rolls_back_entry_and_retry_delivers(self, manager: SessionManager):
        """投递失败即受理失败：条目补偿删除，同幂等键重试重新受理并送达 SDK。"""
        meta = await manager.meta_store.create("demo", SDK_ID)

        class _FlakyQueryClient(FakeSDKClient):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.fail_next_query = True

            async def query(self, prompt, session_id: str = "default") -> None:
                if self.fail_next_query:
                    self.fail_next_query = False
                    raise RuntimeError("transport down")
                await super().query(prompt, session_id)

        client = _FlakyQueryClient(
            messages=[{"type": "result", "subtype": "success", "session_id": SDK_ID, "uuid": "r-1"}]
        )
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch("server.agent_runtime.session_manager.tag_session", None),
        ):
            with pytest.raises(RuntimeError):
                await manager.send_message(
                    SDK_ID,
                    "继续",
                    meta=meta,
                    user_entry=build_user_entry([{"type": "text", "text": "继续"}]),
                    client_key="ck-fail",
                )
            try:
                # 受理条目已回滚，日志无残留
                assert await manager.event_log_store.list_after(SDK_ID) == []

                # actor 已随失败退出；关闭会话模拟冷恢复后重试
                await manager.close_session(SDK_ID)
                retry = await manager.send_message(
                    SDK_ID,
                    "继续",
                    meta=meta,
                    user_entry=build_user_entry([{"type": "text", "text": "继续"}]),
                    client_key="ck-fail",
                )
                # 重试重新受理（seq 重新分配）且真正送达 SDK
                assert retry is not None
                assert retry["seq"] == 0
                assert client.sent_queries == ["继续"]
            finally:
                await manager.close_session(SDK_ID)
