"""Unit tests for SessionManager user-input behavior: handing messages to the CLI and recognizing its replays."""

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock

import pytest

from server.agent_runtime.session_manager import SDK_AVAILABLE, AgentStartupError, ManagedSession, SessionManager
from tests.fakes import (
    assistant_frame,
    build_managed_with_actor,
    replay_frame,
    result_frame,
    stream_event_frame,
    system_frame,
)


def _drain(queue: asyncio.Queue) -> list[dict]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


async def _seed(session_manager, meta_store, *, frames=None, status="idle"):
    """Create a session meta + pre-connected managed session with actor + FakeSDKClient."""
    meta = await meta_store.create("demo", "sdk-user-input")
    await meta_store.update_status(meta.id, status)

    # 读取回调用 SessionManager 的生产实现，result 收尾与 CLI 状态帧都走真实路径。
    callbacks: list = []

    def _on_message(m, msg):
        if not callbacks:
            callbacks.append(session_manager._make_actor_message_callback([m]))
        callbacks[0](msg)

    managed, actor, client = await build_managed_with_actor(
        session_id=meta.id,
        project_name="demo",
        status=status,
        frames=frames,
        on_message_hook=_on_message,
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
            with contextlib.suppress(Exception):
                managed._inbox.put_nowait(None)

        actor._task.add_done_callback(_done_cb)
    return meta, managed, client


async def _finish(managed):
    """Graceful teardown."""
    with contextlib.suppress(Exception):
        await managed.send_disconnect()
    if managed._process_task is not None and not managed._process_task.done():
        try:
            await asyncio.wait_for(managed._process_task, timeout=2.0)
        except (TimeoutError, BaseException):
            managed._process_task.cancel()
            with contextlib.suppress(BaseException):
                await managed._process_task


class TestSessionManagerUserInput:
    async def test_send_message_hands_the_message_to_the_cli_as_a_queued_message(self, session_manager, meta_store):
        meta, managed, client = await _seed(session_manager, meta_store)
        try:
            queue = managed.channel.subscribe()
            accepted = await session_manager.send_message(meta.id, "hello realtime")

            assert client.sent_queries == ["hello realtime"]
            # 不广播本地合成消息：CLI 开始处理它之前只作为排队消息下发
            assert _drain(queue) == [{"type": "queued_message", "op": "upsert", "message": accepted["queued_message"]}]
        finally:
            await _finish(managed)

    async def test_image_only_replay_is_recognized_by_its_uuid(self, session_manager, meta_store):
        """SDK 解析器丢掉 image 块，正文为空的带图消息回放时内容为空，只能按 uuid 认出。"""
        meta, managed, client = await _seed(session_manager, meta_store)
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
        try:
            await session_manager.send_message(meta.id, [image])
            assert client.sent_messages[0]["message"]["content"] == [image]

            queue = managed.channel.subscribe()
            client.push_frame(replay_frame())
            client.push_frame(assistant_frame({"type": "text", "text": "看到了"}, uuid="a-1"))
            broadcast = [await asyncio.wait_for(queue.get(), timeout=1.0)]

            # 回放不作为用户消息广播，下一条广播就是 Agent 的回复
            assert [message.get("type") for message in broadcast] == ["assistant"]
        finally:
            await _finish(managed)

    async def test_cli_idle_after_result_settles_status(self, session_manager, meta_store):
        frames = [
            stream_event_frame(
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}, uuid="stream-1"
            ),
            assistant_frame({"type": "text", "text": "Hello"}, uuid="assistant-1"),
            result_frame(uuid="result-1"),
            system_frame("session_state_changed", state="idle"),
        ]
        meta, managed, _client = await _seed(session_manager, meta_store, frames=frames, status="idle")
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

    async def test_closing_a_session_withdraws_its_queued_messages(self, session_manager, meta_store):
        """CLI 已断开，排队消息不会再被处理：移出列表，并告知跨驱逐存活的订阅者。"""
        meta, managed, _client = await _seed(session_manager, meta_store, status="running")
        accepted = await session_manager.send_message(meta.id, "等一下再做")
        queue = managed.channel.subscribe()

        await session_manager.close_session(meta.id)

        assert {"type": "queued_message", "op": "remove", "id": accepted["queued_message"]["id"]} in _drain(queue)

    async def test_cleanup_on_error_disconnect_timeout_does_not_block(
        self, session_manager, meta_store, monkeypatch, caplog
    ):
        """启动失败清理路径里 send_disconnect 挂起时，超时兜底让清理仍在限时内完成。"""
        proj_dir = session_manager.layout.projects_dir / "demo"
        proj_dir.mkdir(parents=True)
        (proj_dir / "project.json").write_text('{"title": "t"}', encoding="utf-8")

        seen_commands: list[str] = []
        cancelled: list[bool] = []

        class _FakeActor:
            def __init__(self, *_, **__):
                self.task = None

            async def start(self):
                return None

            def add_done_callback(self, _cb):
                pass

            async def enqueue(self, cmd):
                seen_commands.append(cmd.type)
                if cmd.type == "query":
                    cmd.error = RuntimeError("SDK 拒绝了这次投递")
                    cmd.done.set()
                elif cmd.type == "disconnect":
                    # 模拟 SDK 侧挂起：投递就卡住，send_disconnect 连 cmd.done 都等不到，
                    # 只有 asyncio.wait_for 的超时能让 _cleanup_on_error 脱身。
                    await asyncio.Event().wait()
                else:
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

        with (
            caplog.at_level(logging.WARNING, logger="server.agent_runtime.session_manager"),
            pytest.raises(AgentStartupError, match="SDK 拒绝了这次投递"),
        ):
            await asyncio.wait_for(session_manager.send_new_session("demo", "你好"), timeout=2.0)

        assert "disconnect" in seen_commands
        assert any("超时" in r.getMessage() for r in caplog.records)
        # 断开挂起时 actor 必须被取消，否则协程随失败的会话一起泄漏。
        assert cancelled == [True]
        # 超时不阻断后续清理：会话从注册表摘除。
        assert session_manager.sessions == {}

    async def test_cleanup_on_error_disconnect_timeout_settles_the_startup_failure_as_error(
        self, meta_store, monkeypatch, tmp_path
    ):
        """会话跑起来后才启动失败、断开又挂起时，终态是 error 而不是 interrupted。"""
        session_manager = SessionManager(project_root=tmp_path, meta_store=meta_store, sdk_id_timeout=0.05)
        proj_dir = session_manager.layout.projects_dir / "demo"
        proj_dir.mkdir(parents=True)
        (proj_dir / "project.json").write_text('{"title": "t"}', encoding="utf-8")

        captured_sessions: list[ManagedSession] = []

        class _FakeActor:
            def __init__(self, *_, **__):
                self.task = None

            async def start(self):
                return None

            def add_done_callback(self, _cb):
                pass

            async def enqueue(self, cmd):
                if cmd.type == "query":
                    # 投递成功：status 落到 "running"，但 SDK 始终不回 init 消息，
                    # 会话卡在等 sdk_session_id，最终由超时走进 _cleanup_on_error。
                    captured_sessions.extend(session_manager.sessions.values())
                    cmd.done.set()
                elif cmd.type == "disconnect":
                    await asyncio.Event().wait()
                else:
                    cmd.done.set()

            async def wait(self):
                return None

            async def cancel_and_wait(self):
                return None

        async def fake_env():
            return {"ANTHROPIC_API_KEY": "sk"}

        monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", fake_env)
        monkeypatch.setattr("server.agent_runtime.session_manager.SessionActor", _FakeActor)
        monkeypatch.setattr(type(session_manager), "_ensure_capacity", AsyncMock(return_value=None))
        session_manager._session_actor_shutdown_timeout = 0.05

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(session_manager.send_new_session("demo", "你好"), timeout=2.0)

        # 启动失败不是中断：终态不写 interrupted。
        assert captured_sessions[0].status == "error"

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
            with pytest.raises(ValueError, match=r"未找到待回答的问题"):
                await session_manager.answer_user_question(
                    session_id=meta.id,
                    question_id="missing-question-id",
                    answers={"Q": "A"},
                )
        finally:
            await _finish(managed)

    async def test_interrupt_session_requests_interrupt_and_keeps_consumer_alive(self, session_manager, meta_store):
        # 替身不发 result 帧，这一轮在 interrupt 前后都保持在途
        meta, managed, client = await _seed(session_manager, meta_store, status="running")
        try:
            await session_manager.send_message(meta.id, "prompt")

            new_status = await session_manager.interrupt_session(meta.id)

            # interrupt_session returns whatever managed.status is after send_interrupt.
            # Without a result message, status stays "running".
            assert client.interrupted
            assert managed.interrupt_requested
            assert new_status in ("running", "interrupted")
            # Consumer/actor task should still be alive (not cancelled).
            assert managed.actor._task is not None
            assert not managed.actor._task.done()
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
