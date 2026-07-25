"""FakeSDKClient 驱动会话：事件日志端到端产出（验收：seq 单调、类型正确）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from server.agent_runtime.event_log import EventLogStore, build_user_entry
from server.agent_runtime.session_manager import AgentStartupError, SessionManager
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


async def _wait_for_status(manager: SessionManager, session_id: str, status: str, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        managed = manager.sessions.get(session_id)
        if managed is not None and managed.status == status:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"session {session_id} did not reach status {status!r} within {timeout}s")


class _InterruptingClient(FakeSDKClient):
    """interrupt() 时按真实 CLI 行为注入中断回显（可选）与 result 消息。"""

    def __init__(self, *, echo: bool = True):
        super().__init__(messages=[], block_forever=True)
        self._echo = echo

    async def interrupt(self) -> None:
        self._record("interrupt")
        self.interrupted = True
        if self._echo:
            await self._pending_messages.put(
                {"type": "user", "content": "[Request interrupted by user]", "uuid": "sdk-echo-1", "session_id": SDK_ID}
            )
        await self._pending_messages.put(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "uuid": "r-int",
                "session_id": SDK_ID,
            }
        )
        await self._pending_messages.put(None)


class _CrashBeforeInitClient(FakeSDKClient):
    def __init__(self, stderr_callback=None):
        super().__init__()
        self._stderr_callback = stderr_callback

    async def receive_response(self):
        self._record("receive_response")
        if self._stderr_callback is not None:
            self._stderr_callback("OPENAI_API_KEY=pre-init-secret\nprovider stderr detail")
        if False:
            yield {}
        raise RuntimeError("receive_response crashed before init")


class _QueryFailureClient(FakeSDKClient):
    async def query(self, prompt, session_id: str = "default") -> None:
        self._record("query")
        raise RuntimeError("query rejected before session init")


class TestNewSessionEventLogFlow:
    async def test_receive_crash_before_init_is_reported_as_structured_startup_failure(self, manager: SessionManager):
        captured_stderr: list = []

        async def build_options(*_args, **kwargs):
            captured_stderr.append(kwargs["stderr"])
            return SimpleNamespace(env=None)

        client = _CrashBeforeInitClient(lambda line: captured_stderr[0](line))

        with (
            patch.object(manager, "_build_options", new=build_options),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
        ):
            with pytest.raises(AgentStartupError) as exc_info:
                await manager.send_new_session("demo", "hello")

        assert exc_info.value.failure_observation is not None
        assert exc_info.value.failure_observation["phase"] == "startup"
        assert exc_info.value.failure_observation["summary"]["message"] == "receive_response crashed before init"
        assert exc_info.value.failure_observation["raw"]["sdk_stderr"] == (
            "OPENAI_API_KEY=••••\nprovider stderr detail"
        )

    async def test_query_failure_before_init_is_reported_as_structured_startup_failure(self, manager: SessionManager):
        client = _QueryFailureClient()

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=SimpleNamespace(env=None))),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
        ):
            with pytest.raises(AgentStartupError) as exc_info:
                await manager.send_new_session("demo", "hello")

        assert exc_info.value.failure_observation is not None
        assert exc_info.value.failure_observation["phase"] == "startup"
        assert exc_info.value.failure_observation["summary"]["message"] == "query rejected before session init"

    async def test_inbox_processor_failure_before_init_is_cleaned_up_and_reported(self, manager: SessionManager):
        client = FakeSDKClient()

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=SimpleNamespace(env=None))),
            patch.object(
                manager,
                "_process_inbox",
                new=AsyncMock(side_effect=RuntimeError("inbox processor crashed before init")),
            ),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
        ):
            with pytest.raises(AgentStartupError) as exc_info:
                await manager.send_new_session("demo", "hello")

        assert manager.sessions == {}
        assert exc_info.value.failure_observation is not None
        assert exc_info.value.failure_observation["summary"]["message"] == "inbox processor crashed before init"

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

    async def test_sdk_error_becomes_one_turn_failure_event_with_raw_assistant_and_result(
        self,
        manager: SessionManager,
        caplog: pytest.LogCaptureFixture,
    ):
        upstream_message = (
            "There's an issue with the selected model (gpt-5.6-sol). It may not exist or you may not have access to it."
        )
        assistant_error = {
            "type": "assistant",
            "message_id": "msg-error",
            "uuid": "a-error",
            "timestamp": "2026-07-23T01:02:03Z",
            "session_id": SDK_ID,
            "model": "<synthetic>",
            "error": "invalid_request",
            "stop_reason": "stop_sequence",
            "content": [{"type": "text", "text": upstream_message}],
            "future_sdk_field": {"kept": "verbatim", "api_key": "sk-ant-api03-secret-value"},
        }
        result_error = {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "api_error_status": 404,
            "errors": ["upstream request failed"],
            "session_id": SDK_ID,
            "uuid": "r-error",
            "future_result_field": {"request_id": "req-visible"},
        }
        client = FakeSDKClient(
            messages=[
                {"type": "system", "subtype": "init", "session_id": SDK_ID, "uuid": "init-error"},
                {"type": "user", "content": "你好", "uuid": "sdk-u-error", "session_id": SDK_ID},
                assistant_error,
                result_error,
            ]
        )
        fake_options = SimpleNamespace(env=None)
        caplog.set_level("WARNING", logger="server.agent_runtime.entry_pipeline")

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch("server.agent_runtime.session_manager.tag_session", None),
        ):
            await manager.send_new_session(
                "demo",
                "你好",
                user_entry=build_user_entry([{"type": "text", "text": "你好"}]),
                client_key="ck-error",
            )

        try:
            entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 2)
            await _wait_for_status(manager, SDK_ID, "error")
            assert [entry["type"] for entry in entries] == ["user", "system"]

            failure_entry = entries[1]
            assert failure_entry["subtype"] == "agent_turn_failure"
            failure = failure_entry["failure"]
            assert failure["phase"] == "turn"
            assert failure["project_name"] == "demo"
            assert failure["session_id"] == SDK_ID
            assert failure["summary"] == {
                "source": "sdk_assistant",
                "type": "invalid_request",
                "status": 404,
                "message": upstream_message,
            }
            assert failure["raw"]["assistant_message"]["model"] == "<synthetic>"
            assert failure["raw"]["assistant_message"]["future_sdk_field"] == {
                "kept": "verbatim",
                "api_key": "••••",
            }
            assert failure["raw"]["result_message"]["future_result_field"] == {"request_id": "req-visible"}
            assert all(entry["type"] != "assistant" for entry in entries)

            rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
            assert "failure_observation=" in rendered_logs
            assert "invalid_request" in rendered_logs
            assert upstream_message in rendered_logs
            assert "sk-ant-api03-secret-value" not in rendered_logs
        finally:
            await manager.close_session(SDK_ID)

    async def test_interrupt_flow_produces_single_typed_entry(self, manager: SessionManager):
        """中断动作与结果是时间线事件：SDK 回显 + result 兜底（竞态双写）只产出一条 typed 条目。"""
        meta = await manager.meta_store.create("demo", SDK_ID)
        client = _InterruptingClient(echo=True)
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch("server.agent_runtime.session_manager.tag_session", None),
        ):
            await manager.send_message(
                SDK_ID,
                "写分镜",
                meta=meta,
                user_entry=build_user_entry([{"type": "text", "text": "写分镜"}]),
                client_key="ck-int",
            )
            await _wait_for_status(manager, SDK_ID, "running")  # 让 query 进入 drain
            await manager.interrupt_session(SDK_ID)

        try:
            entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 2)
            await _wait_for_status(manager, SDK_ID, "interrupted")
            assert [e["type"] for e in entries] == ["user", "system"]
            assert entries[1]["subtype"] == "interrupt"
            assert manager.sessions[SDK_ID].status == "interrupted"
            # result 兜底与回显竞态不产生第二条中断条目
            final_entries = await manager.event_log_store.list_after(SDK_ID)
            assert [e["type"] for e in final_entries] == ["user", "system"]
        finally:
            await manager.close_session(SDK_ID)

    async def test_interrupt_without_sdk_echo_still_produces_typed_entry(self, manager: SessionManager):
        """回显缺席的中断：result(session_status=interrupted) 兜底定型，时间线仍有稳定条目。"""
        meta = await manager.meta_store.create("demo", SDK_ID)
        client = _InterruptingClient(echo=False)
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch("server.agent_runtime.session_manager.tag_session", None),
        ):
            await manager.send_message(
                SDK_ID,
                "写分镜",
                meta=meta,
                user_entry=build_user_entry([{"type": "text", "text": "写分镜"}]),
                client_key="ck-int2",
            )
            await _wait_for_status(manager, SDK_ID, "running")
            await manager.interrupt_session(SDK_ID)

        try:
            entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 2)
            assert [e["type"] for e in entries] == ["user", "system"]
            assert entries[1]["subtype"] == "interrupt"
        finally:
            await manager.close_session(SDK_ID)

    async def test_ask_user_question_flow_produces_question_and_answer_entries(self, manager: SessionManager):
        """AskUserQuestion 提问（assistant tool_use）与答复（typed 答复条目）都出现在日志。"""
        meta = await manager.meta_store.create("demo", SDK_ID)
        client = FakeSDKClient(
            messages=[
                {
                    "type": "assistant",
                    "message_id": "msg_q",
                    "uuid": "a-q",
                    "session_id": SDK_ID,
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu-q",
                            "name": "AskUserQuestion",
                            "input": {"questions": [{"question": "继续吗?", "options": [{"label": "继续"}]}]},
                        }
                    ],
                },
                {
                    "type": "user",
                    "uuid": "u-ans",
                    "session_id": SDK_ID,
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu-q",
                            "content": 'Your questions have been answered: "继续吗?"="继续".',
                        }
                    ],
                    "tool_use_result": {"questions": [], "answers": {"继续吗?": "继续"}, "annotations": {}},
                },
                {"type": "result", "subtype": "success", "is_error": False, "session_id": SDK_ID, "uuid": "r-1"},
            ]
        )
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch("server.agent_runtime.session_manager.tag_session", None),
        ):
            await manager.send_message(
                SDK_ID,
                "开始",
                meta=meta,
                user_entry=build_user_entry([{"type": "text", "text": "开始"}]),
                client_key="ck-q",
            )

        try:
            entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 3)
            assert [e["type"] for e in entries] == ["user", "assistant", "user"]
            # 提问条目：assistant 条目内的 AskUserQuestion tool_use（结构化 questions）
            assert entries[1]["content"][0]["name"] == "AskUserQuestion"
            # 答复条目：typed、携带结构化答案与 tool_use_id 关联
            assert entries[2]["subtype"] == "question_answer"
            assert entries[2]["tool_use_id"] == "tu-q"
            assert entries[2]["answers"] == {"继续吗?": "继续"}
            assert entries[2]["is_error"] is False
        finally:
            await manager.close_session(SDK_ID)

    async def test_task_notification_flow_produces_typed_entries(self, manager: SessionManager):
        """task 通知双通道（typed 系统消息 + 注入 XML 用户消息）都定型为 system 条目，无通用 user 条目。"""
        xml = (
            "<task-notification>\n<task-id>t1</task-id>\n<tool-use-id>tu-a</tool-use-id>\n"
            "<status>completed</status>\n<summary>分析完成</summary>\n</task-notification>"
        )
        meta = await manager.meta_store.create("demo", SDK_ID)
        client = FakeSDKClient(
            messages=[
                {
                    "type": "system",
                    "subtype": "task_started",
                    "task_id": "t1",
                    "description": "分析",
                    "tool_use_id": "tu-a",
                    "uuid": "s-1",
                    "session_id": SDK_ID,
                },
                {"type": "user", "content": xml, "uuid": "n-1", "session_id": SDK_ID},
                {"type": "result", "subtype": "success", "is_error": False, "session_id": SDK_ID, "uuid": "r-1"},
            ]
        )
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch("server.agent_runtime.session_manager.tag_session", None),
        ):
            await manager.send_message(
                SDK_ID,
                "跑子任务",
                meta=meta,
                user_entry=build_user_entry([{"type": "text", "text": "跑子任务"}]),
                client_key="ck-t",
            )

        try:
            entries = await _wait_for_entries(manager.event_log_store, SDK_ID, 3)
            assert [e["type"] for e in entries] == ["user", "system", "system"]
            assert entries[1]["subtype"] == "task_started"
            assert entries[2]["subtype"] == "task_notification"
            assert entries[2]["task_id"] == "t1"
            assert entries[2]["task_status"] == "completed"
            assert entries[2]["summary"] == "分析完成"
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

    async def test_initial_user_entry_write_failure_reports_error(self, manager: SessionManager):
        """新会话首条用户消息落库失败：受理显式失败，会话进入可观察的 error 态。"""
        client = FakeSDKClient(messages=_new_session_messages())
        fake_options = SimpleNamespace(env=None)
        update_status_spy = AsyncMock(wraps=manager.meta_store.update_status)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch("server.agent_runtime.session_manager.tag_session", None),
            patch.object(
                manager.event_log_store,
                "append_user_entry",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch.object(manager.meta_store, "update_status", new=update_status_spy),
        ):
            with pytest.raises(RuntimeError):
                await manager.send_new_session(
                    "demo",
                    "帮我写分镜",
                    user_entry=build_user_entry([{"type": "text", "text": "帮我写分镜"}]),
                    client_key="ck-fail-new",
                )

        # 状态回写：会话进入可观察的 error 态而非静默成功
        meta = await manager.meta_store.get(SDK_ID)
        assert meta is not None
        assert meta.status == "error"
        # 会话已随失败清理：不残留内存态、SDK 连接已断开
        assert SDK_ID not in manager.sessions
        assert client.disconnected is True
        # 内存态提前置 error：cleanup 取消 _process_task 时不应再走 interrupted
        # 分支多写一次终态（否则 DB 会先落 interrupted 再落 error）
        statuses_written = [call.args[1] for call in update_status_spy.call_args_list]
        assert "interrupted" not in statuses_written
        assert statuses_written.count("error") == 1

    async def test_initial_user_entry_failure_skips_finalize_for_early_result(self, manager: SessionManager):
        """首条用户消息落库失败后，同一轮内紧跟到达的 result 不应被 finalize：
        否则会先广播/落库非 error 终态（如 completed），随后又被错误清理路径改写为
        error，造成状态短暂跳变。"""
        client = FakeSDKClient(
            messages=[
                {"type": "system", "subtype": "init", "session_id": SDK_ID, "uuid": "init-1"},
                {"type": "result", "subtype": "success", "is_error": False, "session_id": SDK_ID, "uuid": "r-1"},
            ]
        )
        fake_options = SimpleNamespace(env=None)
        update_status_spy = AsyncMock(wraps=manager.meta_store.update_status)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: client),
            patch("server.agent_runtime.session_manager.tag_session", None),
            patch.object(
                manager.event_log_store,
                "append_user_entry",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch.object(manager.meta_store, "update_status", new=update_status_spy),
        ):
            with pytest.raises(RuntimeError):
                await manager.send_new_session(
                    "demo",
                    "帮我写分镜",
                    user_entry=build_user_entry([{"type": "text", "text": "帮我写分镜"}]),
                    client_key="ck-fail-early-result",
                )

        meta = await manager.meta_store.get(SDK_ID)
        assert meta is not None
        assert meta.status == "error"
        # "running" 是新会话建立时的常规写入；result 被短路未 finalize，
        # 不应出现 finalize 才会写入的 completed/idle 等中间终态
        statuses_written = [call.args[1] for call in update_status_spy.call_args_list]
        assert statuses_written == ["running", "error"]

    async def test_initial_user_entry_retry_after_failure_delivers(self, manager: SessionManager):
        """落库失败后同幂等键重试：条目无残留短路，重试重新受理并分配 seq 0。"""
        retry_sdk_id = "sdk-e2e-retry"
        failing_client = FakeSDKClient(messages=_new_session_messages())
        retry_client = FakeSDKClient(
            messages=[
                {"type": "system", "subtype": "init", "session_id": retry_sdk_id, "uuid": "init-2"},
                {"type": "result", "subtype": "success", "is_error": False, "session_id": retry_sdk_id, "uuid": "r-2"},
            ]
        )
        clients = iter([failing_client, retry_client])
        fake_options = SimpleNamespace(env=None)

        with (
            patch.object(manager, "_build_options", new=AsyncMock(return_value=fake_options)),
            patch("server.agent_runtime.session_manager.ClaudeSDKClient", lambda options: next(clients)),
            patch("server.agent_runtime.session_manager.tag_session", None),
        ):
            with patch.object(
                manager.event_log_store,
                "append_user_entry",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ):
                with pytest.raises(RuntimeError):
                    await manager.send_new_session(
                        "demo",
                        "帮我写分镜",
                        user_entry=build_user_entry([{"type": "text", "text": "帮我写分镜"}]),
                        client_key="ck-retry-new",
                    )

            # 失败会话（SDK_ID）本身无任何条目残留：append 写入真失败，非部分写入
            assert not await manager.event_log_store.has_entries(SDK_ID)

            sdk_id = await manager.send_new_session(
                "demo",
                "帮我写分镜",
                user_entry=build_user_entry([{"type": "text", "text": "帮我写分镜"}]),
                client_key="ck-retry-new",
            )
        try:
            assert sdk_id == retry_sdk_id
            managed = manager.sessions[retry_sdk_id]
            assert managed.initial_user_log_entry is not None
            assert managed.initial_user_log_entry["seq"] == 0
            assert managed.initial_user_entry_error is None
            # 同幂等键在重试会话下正确解析到新写入的条目，未短路到失败会话
            resolved = await manager.event_log_store.find_by_client_key(retry_sdk_id, "ck-retry-new")
            assert resolved == managed.initial_user_log_entry
        finally:
            await manager.close_session(retry_sdk_id)
