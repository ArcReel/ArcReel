"""Unit tests for SessionManager user-input and user-echo behavior."""

import asyncio

import pytest

from server.agent_runtime.session_manager import (
    SDK_AVAILABLE,
    ManagedSession,
)
from tests.fakes import FakeSDKClient


class TestSessionManagerUserInput:
    async def test_send_message_adds_user_echo_to_buffer(self, session_manager, meta_store):
        meta = await meta_store.create("demo", "sdk-user-input")
        client = FakeSDKClient()
        managed = ManagedSession(
            session_id=meta.id,
            client=client,
            status="idle",
        )
        session_manager.sessions[meta.id] = managed

        await session_manager.send_message(meta.id, "hello realtime")
        assert client.sent_queries == ["hello realtime"]
        assert len(managed.message_buffer) >= 1
        echo = managed.message_buffer[0]
        assert echo.get("type") == "user"
        assert echo.get("content") == "hello realtime"
        assert echo.get("local_echo")

        if managed.consumer_task:
            await managed.consumer_task

    async def test_send_message_prunes_previous_stream_events(self, session_manager, meta_store):
        meta = await meta_store.create("demo", "sdk-user-input")
        client = FakeSDKClient()
        managed = ManagedSession(
            session_id=meta.id,
            client=client,
            status="idle",
            message_buffer=[
                {
                    "type": "assistant",
                    "content": [{"type": "text", "text": "previous reply"}],
                    "uuid": "assistant-old-1",
                },
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "old delta"},
                    },
                    "uuid": "stream-old-1",
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "uuid": "result-old-1",
                },
            ],
        )
        session_manager.sessions[meta.id] = managed

        await session_manager.send_message(meta.id, "new question")
        if managed.consumer_task:
            await managed.consumer_task

        assert not any(msg.get("type") == "stream_event" for msg in managed.message_buffer)
        # assistant/result are also pruned (persisted in SDK transcript, kept causes dupes)
        assert not any(msg.get("type") == "assistant" for msg in managed.message_buffer)
        assert not any(msg.get("type") == "result" for msg in managed.message_buffer)

    async def test_consume_result_prunes_stream_events_after_completion(self, session_manager, meta_store):
        meta = await meta_store.create("demo", "sdk-user-input")
        client = FakeSDKClient(
            messages=[
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
        )
        managed = ManagedSession(
            session_id=meta.id,
            client=client,
            status="running",
        )
        session_manager.sessions[meta.id] = managed
        await meta_store.update_status(meta.id, "running")

        await session_manager._consume_messages(managed)
        assert managed.status == "completed"
        assert not any(msg.get("type") == "stream_event" for msg in managed.message_buffer)
        # assistant/result are also pruned (persisted in SDK transcript, kept causes dupes)
        assert not any(msg.get("type") == "assistant" for msg in managed.message_buffer)
        assert not any(msg.get("type") == "result" for msg in managed.message_buffer)

    async def test_ask_user_question_waits_for_answer_and_merges_answers(self, session_manager, meta_store):
        if not SDK_AVAILABLE:
            pytest.skip("claude_agent_sdk is not installed")

        meta = await meta_store.create("demo", "sdk-user-input")
        managed = ManagedSession(
            session_id=meta.id,
            client=FakeSDKClient(),
            status="running",
        )
        session_manager.sessions[meta.id] = managed

        callback = await session_manager._build_can_use_tool_callback(meta.id)

        question_input = {
            "questions": [
                {
                    "question": "select duration",
                    "header": "Duration",
                    "multiSelect": False,
                    "options": [
                        {"label": "2 minutes", "description": "shorter"},
                        {"label": "4 minutes", "description": "more complete"},
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
            answers={"select duration": "2 minutes"},
        )

        allow_result = await task
        assert allow_result.updated_input.get("answers", {}).get("select duration") == "2 minutes"

    async def test_answer_user_question_raises_for_unknown_question(self, session_manager, meta_store):
        meta = await meta_store.create("demo", "sdk-user-input")
        managed = ManagedSession(
            session_id=meta.id,
            client=FakeSDKClient(),
            status="running",
        )
        session_manager.sessions[meta.id] = managed

        with pytest.raises(ValueError):
            await session_manager.answer_user_question(
                session_id=meta.id,
                question_id="missing-question-id",
                answers={"Q": "A"},
            )

    async def test_interrupt_session_requests_interrupt_and_keeps_consumer_alive(self, session_manager, meta_store):
        meta = await meta_store.create("demo", "sdk-user-input")
        client = FakeSDKClient()
        managed = ManagedSession(
            session_id=meta.id,
            client=client,
            status="running",
        )
        session_manager.sessions[meta.id] = managed
        await meta_store.update_status(meta.id, "running")

        new_status = await session_manager.interrupt_session(meta.id)
        assert new_status == "running"
        assert client.interrupted
        assert managed.status == "running"
        assert managed.interrupt_requested
        assert len(managed.message_buffer) == 0
        stored = await meta_store.get(meta.id)
        assert stored is not None
        assert stored.status == "running"

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
