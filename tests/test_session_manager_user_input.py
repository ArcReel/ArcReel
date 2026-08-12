"""Unit tests for SessionManager user-input and user-echo behavior."""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from server.agent_runtime.event_log import REPLAYED_USER_ECHO_ENTRY_UUID_KEY, REPLAYED_USER_ECHO_KEY
from server.agent_runtime.message_serialization import PendingUserEcho, match_user_echo, message_to_dict
from server.agent_runtime.session_manager import SDK_AVAILABLE, AgentStartupError
from tests.fakes import build_managed_with_actor

pytestmark = pytest.mark.unit


async def _seed(session_manager, meta_store, *, messages=None, status="idle", block_forever=False):
    """Create a session meta + pre-connected managed session with actor + FakeSDKClient."""
    meta = await meta_store.create("demo", "sdk-user-input")
    await meta_store.update_status(meta.id, status)

    # Build actor with the on_message hook that mirrors SessionManager's production path
    # so ResultMessage finalization and stream pruning work end-to-end.
    managed, actor, client = await build_managed_with_actor(
        session_id=meta.id,
        project_name="demo",
        status=status,
        messages=messages,
        block_forever=block_forever,
        on_message_hook=lambda m, msg: _on_actor_message_full(session_manager, m, msg),
    )
    managed.resolved_sdk_id = meta.id
    managed.sdk_id_event.set()
    session_manager.sessions[meta.id] = managed
    # spawn inbox processor so _finalize_turn runs on result messages
    managed._process_task = asyncio.create_task(
        session_manager._process_inbox(managed),
        name=f"inbox-{meta.id}",
    )
    # Ensure inbox sentinel is pushed when actor ends.
    if actor._task is not None:

        def _done_cb(_t):
            try:
                managed._inbox.put_nowait(None)
            except Exception:
                pass

        actor._task.add_done_callback(_done_cb)
    return meta, managed, client


def _on_actor_message_full(session_manager, managed, raw_msg):
    """Replicate SessionManager's production on_message behavior for tests."""
    msg_dict = message_to_dict(raw_msg)
    if not isinstance(msg_dict, dict):
        return
    echo = match_user_echo(managed.pending_user_echoes, msg_dict)
    if echo is not None:
        msg_dict[REPLAYED_USER_ECHO_KEY] = True
        if echo.entry_uuid:
            msg_dict[REPLAYED_USER_ECHO_ENTRY_UUID_KEY] = echo.entry_uuid
        managed._inbox.put_nowait(msg_dict)
        return
    session_manager._handle_special_message(managed, msg_dict)
    managed._on_actor_message(msg_dict)
    managed._inbox.put_nowait(msg_dict)


async def _finish(managed):
    """Graceful teardown."""
    try:
        await managed.send_disconnect()
    except Exception:
        pass
    if managed._process_task is not None and not managed._process_task.done():
        try:
            await asyncio.wait_for(managed._process_task, timeout=2.0)
        except (TimeoutError, BaseException):
            managed._process_task.cancel()
            try:
                await managed._process_task
            except BaseException:
                pass


class TestSessionManagerUserInput:
    async def test_send_message_registers_pending_echo_and_sends_query(self, session_manager, meta_store):
        # Result message so the actor exits cleanly after query.
        messages = [{"type": "result", "subtype": "success", "is_error": False, "uuid": "r1"}]
        meta, managed, client = await _seed(session_manager, meta_store, messages=messages)
        try:
            queue = managed.channel.subscribe()
            await session_manager.send_message(meta.id, "hello realtime")
            assert client.sent_queries == ["hello realtime"]
            # 不再广播本地合成 echo：受理回显由权威日志条目承担，
            # pending_user_echoes 仅用于给 SDK 回放副本打标（写入点跳过）。
            broadcasted = []
            while not queue.empty():
                broadcasted.append(queue.get_nowait())
            assert not any(isinstance(item, dict) and item.get("local_echo") for item in broadcasted)
        finally:
            await _finish(managed)

    async def test_consume_result_finalizes_status(self, session_manager, meta_store):
        messages = [
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Hello"},
                },
                "uuid": "stream-1",
            },
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "Hello"}],
                "uuid": "assistant-1",
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "uuid": "result-1",
            },
        ]
        meta, managed, client = await _seed(session_manager, meta_store, messages=messages, status="idle")
        try:
            await session_manager.send_message(meta.id, "hi")

            # send_message 在 prompt 送入 SDK 即返回；等 actor 后台 drain 与 inbox 处理完成。
            for _ in range(200):
                await asyncio.sleep(0)
                if managed.status != "running" and managed._inbox.empty():
                    break
                await asyncio.sleep(0.01)

            assert managed.status == "completed"
        finally:
            await _finish(managed)

    async def test_unclaimed_echo_replays_are_reported_once_at_turn_end(self, session_manager, meta_store, caplog):
        """回显没被认领 = 该消息会重复落库且缺身份映射；轮次终结时一次性报出残留数。"""
        messages = [{"type": "result", "subtype": "success", "is_error": False, "uuid": "r1"}]
        meta, managed, _client = await _seed(session_manager, meta_store, messages=messages)
        try:
            managed.pending_user_echoes.extend([PendingUserEcho(dedup_key="从未被回放", entry_uuid="user-a")] * 2)

            with caplog.at_level(logging.WARNING, logger="server.agent_runtime.session_manager"):
                await session_manager._finalize_turn(managed, {"type": "result", "subtype": "success"})

            assert managed.pending_user_echoes == []
            assert session_manager.unclaimed_user_echoes == 2
            unclaimed = [r for r in caplog.records if "unclaimed" in r.getMessage()]
            assert len(unclaimed) == 1, "一次 drain 只报一条，不逐条刷屏"
            record = unclaimed[0]
            assert getattr(record, "residue") == 2
            assert getattr(record, "session_id") == managed.session_id
            assert getattr(record, "unclaimed_total") == 2
            assert getattr(record, "reason") == "turn finalized"
        finally:
            await _finish(managed)

    async def test_a_fully_claimed_turn_reports_nothing(self, session_manager, meta_store, caplog):
        """登记被回放副本认领后队列自然排空，终结时无残留可报。"""
        messages = [{"type": "result", "subtype": "success", "is_error": False, "uuid": "r1"}]
        meta, managed, _client = await _seed(session_manager, meta_store, messages=messages)
        try:
            managed.pending_user_echoes.append(PendingUserEcho(dedup_key="你好", entry_uuid="user-a"))
            claimed = match_user_echo(
                managed.pending_user_echoes,
                {"type": "user", "content": "你好"},
            )
            assert claimed is not None and managed.pending_user_echoes == []

            with caplog.at_level(logging.WARNING, logger="server.agent_runtime.session_manager"):
                await session_manager._mark_session_terminal(managed, "interrupted", "user interrupt")

            assert session_manager.unclaimed_user_echoes == 0
            assert not [r for r in caplog.records if "unclaimed" in r.getMessage()]
        finally:
            await _finish(managed)

    async def test_closing_a_running_session_reports_echo_residue(self, session_manager, meta_store, caplog):
        """关停打断进行中的轮次也是轮次终结点，残留照样记账，不因关停路径而漏报。"""
        meta, managed, _client = await _seed(session_manager, meta_store, status="running", block_forever=True)
        managed.status = "running"
        managed.pending_user_echoes.append(PendingUserEcho(dedup_key="没等到回放", entry_uuid="user-a"))

        with caplog.at_level(logging.WARNING, logger="server.agent_runtime.session_manager"):
            await session_manager.close_session(meta.id)

        assert session_manager.unclaimed_user_echoes == 1
        assert managed.pending_user_echoes == [], "记账之后队列要排空，不能只计数"
        unclaimed = [r for r in caplog.records if "unclaimed" in r.getMessage()]
        assert len(unclaimed) == 1
        assert getattr(unclaimed[0], "reason") == "session evicted"

    async def test_failed_new_session_startup_does_not_count_as_unclaimed(
        self, session_manager, meta_store, monkeypatch, caplog, tmp_path
    ):
        """新会话没建起来，回放副本不会抵达：启动失败不计入认领失败、不产生告警。"""
        proj_dir = tmp_path / "projects" / "demo"
        proj_dir.mkdir(parents=True)
        (proj_dir / "project.json").write_text('{"title": "t"}', encoding="utf-8")

        seen_commands: list[str] = []

        class _FakeActor:
            def __init__(self, *_, on_message=None, client_factory=None):
                self.task = None

            async def start(self):
                return None

            def add_done_callback(self, _cb):
                pass

            async def enqueue(self, cmd):
                seen_commands.append(cmd.type)
                if cmd.type == "query":
                    cmd.error = RuntimeError("SDK 拒绝了这次投递")
                cmd.sent.set()
                cmd.done.set()

            async def wait(self):
                return None

        async def fake_env():
            return {"ANTHROPIC_API_KEY": "sk"}

        monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", fake_env)
        monkeypatch.setattr("server.agent_runtime.session_manager.SessionActor", _FakeActor)
        monkeypatch.setattr(type(session_manager), "_ensure_capacity", AsyncMock(return_value=None))

        with caplog.at_level(logging.WARNING, logger="server.agent_runtime.session_manager"):
            with pytest.raises(AgentStartupError, match="SDK 拒绝了这次投递"):
                await session_manager.send_new_session("demo", "你好")

        assert "query" in seen_commands, "投递失败要发生在 query 命令上，而非更早的装配阶段"
        assert session_manager.unclaimed_user_echoes == 0
        assert not [r for r in caplog.records if "unclaimed" in r.getMessage()]

    async def test_cleanup_on_error_disconnect_timeout_does_not_block(
        self, session_manager, meta_store, monkeypatch, caplog, tmp_path
    ):
        """启动失败清理路径里 send_disconnect 挂起时，超时兜底让清理仍在限时内完成。"""
        proj_dir = tmp_path / "projects" / "demo"
        proj_dir.mkdir(parents=True)
        (proj_dir / "project.json").write_text('{"title": "t"}', encoding="utf-8")

        seen_commands: list[str] = []
        cancelled: list[bool] = []

        class _FakeActor:
            def __init__(self, *_, on_message=None, client_factory=None):
                self.task = None

            async def start(self):
                return None

            def add_done_callback(self, _cb):
                pass

            async def enqueue(self, cmd):
                seen_commands.append(cmd.type)
                if cmd.type == "query":
                    cmd.error = RuntimeError("SDK 拒绝了这次投递")
                    cmd.sent.set()
                    cmd.done.set()
                elif cmd.type == "disconnect":
                    # 模拟 SDK 侧挂起：send_disconnect 内部等待的 cmd.done 永不 set，
                    # 只有 asyncio.wait_for 的超时能让 _cleanup_on_error 脱身。
                    await asyncio.Event().wait()
                else:
                    cmd.sent.set()
                    cmd.done.set()

            async def wait(self):
                return None

            async def cancel_and_wait(self):
                cancelled.append(True)

        async def fake_env():
            return {"ANTHROPIC_API_KEY": "sk"}

        monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", fake_env)
        monkeypatch.setattr("server.agent_runtime.session_manager.SessionActor", _FakeActor)
        monkeypatch.setattr(type(session_manager), "_ensure_capacity", AsyncMock(return_value=None))
        session_manager._session_actor_shutdown_timeout = 0.05

        with caplog.at_level(logging.WARNING, logger="server.agent_runtime.session_manager"):
            with pytest.raises(AgentStartupError, match="SDK 拒绝了这次投递"):
                await asyncio.wait_for(session_manager.send_new_session("demo", "你好"), timeout=2.0)

        assert "disconnect" in seen_commands
        assert any("超时" in r.getMessage() for r in caplog.records)
        # 断开挂起时 actor 必须被取消，否则协程随失败的会话一起泄漏。
        assert cancelled == [True]
        # 超时不阻断后续清理：会话从注册表摘除、登记的回放标识清空。
        assert session_manager.sessions == {}
        assert session_manager.unclaimed_user_echoes == 0

    async def test_ask_user_question_waits_for_answer_and_merges_answers(self, session_manager, meta_store):
        if not SDK_AVAILABLE:
            pytest.skip("claude_agent_sdk is not installed")

        meta, managed, _client = await _seed(session_manager, meta_store, status="running")
        try:
            callback = await session_manager._build_can_use_tool_callback(meta.id)

            question_input = {
                "questions": [
                    {
                        "question": "请选择时长",
                        "header": "时长",
                        "multiSelect": False,
                        "options": [
                            {"label": "2分钟", "description": "更短"},
                            {"label": "4分钟", "description": "更完整"},
                        ],
                    }
                ],
                "answers": None,
            }

            queue = managed.channel.subscribe()
            task = asyncio.create_task(callback("AskUserQuestion", question_input, None))
            await asyncio.sleep(0)

            ask_message = queue.get_nowait()
            assert ask_message.get("type") == "ask_user_question"
            question_id = ask_message.get("question_id")
            assert question_id
            assert managed.get_pending_question_payloads()[0]["question_id"] == question_id

            await session_manager.answer_user_question(
                session_id=meta.id,
                question_id=question_id,
                answers={"请选择时长": "2分钟"},
            )

            allow_result = await task
            assert allow_result.updated_input.get("answers", {}).get("请选择时长") == "2分钟"
        finally:
            await _finish(managed)

    async def test_answer_user_question_raises_for_unknown_question(self, session_manager, meta_store):
        meta, managed, _client = await _seed(session_manager, meta_store, status="running")
        try:
            with pytest.raises(ValueError):
                await session_manager.answer_user_question(
                    session_id=meta.id,
                    question_id="missing-question-id",
                    answers={"Q": "A"},
                )
        finally:
            await _finish(managed)

    async def test_interrupt_session_requests_interrupt_and_keeps_consumer_alive(self, session_manager, meta_store):
        # block_forever so actor stays alive through interrupt; we push a result
        # via interrupt() to unblock the drive_query loop.
        meta, managed, client = await _seed(
            session_manager,
            meta_store,
            messages=None,
            status="running",
            block_forever=True,
        )
        # simulate the actor being mid-query. Instead of calling send_message,
        # directly enqueue a query and then interrupt.
        try:
            query_task = asyncio.create_task(managed.send_query("prompt", sdk_session_id=meta.id))
            await asyncio.sleep(0.01)  # let drive_query start

            new_status = await session_manager.interrupt_session(meta.id)

            # interrupt_session returns whatever managed.status is after send_interrupt.
            # Without a result message, status stays "running".
            assert client.interrupted
            assert managed.interrupt_requested
            assert new_status in ("running", "interrupted")
            # Consumer/actor task should still be alive (not cancelled).
            assert managed.actor._task is not None
            assert not managed.actor._task.done()

            # cleanup: push a result to finish the drive_query, then await the query
            client.push_message({"type": "result", "subtype": "error_during_execution", "is_error": True, "uuid": "r1"})
            client.push_message(None)  # sentinel
            await query_task
        finally:
            await _finish(managed)

    def test_resolve_result_status_returns_interrupted_when_interrupt_requested(self, session_manager):
        result = {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "stop_reason": None,
        }
        resolved = session_manager._resolve_result_status(
            result,
            interrupt_requested=True,
        )
        assert resolved == "interrupted"
