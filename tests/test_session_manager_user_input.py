"""Unit tests for SessionManager user-input and user-echo behavior."""

import asyncio

import pytest

from server.agent_runtime.session_manager import SDK_AVAILABLE
from tests.fakes import build_managed_with_actor


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
    msg_dict = session_manager._message_to_dict(raw_msg)
    if not isinstance(msg_dict, dict):
        return
    if session_manager._is_duplicate_user_echo(managed, msg_dict):
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
    async def test_send_message_adds_user_echo_to_buffer(self, session_manager, meta_store):
        # Result message so the actor exits cleanly after query.
        messages = [{"type": "result", "subtype": "success", "is_error": False, "uuid": "r1"}]
        meta, managed, client = await _seed(session_manager, meta_store, messages=messages)
        try:
            await session_manager.send_message(meta.id, "hello realtime")
            assert client.sent_queries == ["hello realtime"]
            assert len(managed.message_buffer) >= 1
            echo = managed.message_buffer[0]
            assert echo.get("type") == "user"
            assert echo.get("content") == "hello realtime"
            assert echo.get("local_echo")
        finally:
            await _finish(managed)

    async def test_send_message_prunes_previous_stream_events(self, session_manager, meta_store):
        messages = [{"type": "result", "subtype": "success", "is_error": False, "uuid": "r1"}]
        meta, managed, client = await _seed(session_manager, meta_store, messages=messages)
        managed.message_buffer.extend(
            [
                {
                    "type": "assistant",
                    "content": [{"type": "text", "text": "上一轮回复"}],
                    "uuid": "assistant-old-1",
                },
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "旧增量"},
                    },
                    "uuid": "stream-old-1",
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "uuid": "result-old-1",
                },
            ]
        )
        try:
            await session_manager.send_message(meta.id, "新问题")

            # Wait briefly for inbox processor to drain the result message.
            for _ in range(100):
                await asyncio.sleep(0)
                if managed._inbox.empty() and not any(msg.get("type") == "result" for msg in managed.message_buffer):
                    break
                await asyncio.sleep(0.01)

            assert not any(msg.get("type") == "stream_event" for msg in managed.message_buffer)
            assert not any(msg.get("type") == "assistant" for msg in managed.message_buffer)
            assert not any(msg.get("type") == "result" for msg in managed.message_buffer)
        finally:
            await _finish(managed)

    async def test_consume_result_prunes_stream_events_after_completion(self, session_manager, meta_store):
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

            # Wait for finalize_turn to complete via inbox processor.
            for _ in range(100):
                await asyncio.sleep(0)
                if managed._inbox.empty() and not any(msg.get("type") == "result" for msg in managed.message_buffer):
                    break
                await asyncio.sleep(0.01)

            assert managed.status == "completed"
            assert not any(msg.get("type") == "stream_event" for msg in managed.message_buffer)
            assert not any(msg.get("type") == "assistant" for msg in managed.message_buffer)
            assert not any(msg.get("type") == "result" for msg in managed.message_buffer)
        finally:
            await _finish(managed)

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

            task = asyncio.create_task(callback("AskUserQuestion", question_input, None))
            await asyncio.sleep(0)

            assert len(managed.message_buffer) >= 1
            ask_message = managed.message_buffer[-1]
            assert ask_message.get("type") == "ask_user_question"
            question_id = ask_message.get("question_id")
            assert question_id

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
