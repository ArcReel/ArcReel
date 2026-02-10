"""Unit tests for SessionManager user-input and user-echo behavior."""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from webui.server.agent_runtime.session_manager import (
    ManagedSession,
    SDK_AVAILABLE,
    SessionManager,
)
from webui.server.agent_runtime.session_store import SessionMetaStore


class FakeClient:
    def __init__(self):
        self.sent_queries: list[str] = []
        self.interrupted = False

    async def query(self, content: str) -> None:
        self.sent_queries.append(content)

    async def interrupt(self) -> None:
        self.interrupted = True

    async def receive_response(self):
        if False:
            yield None


class FakeStreamingClient(FakeClient):
    def __init__(self, messages):
        super().__init__()
        self._messages = list(messages)

    async def receive_response(self):
        for message in self._messages:
            yield message


class TestSessionManagerUserInput(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        tmppath = Path(self.tmpdir.name)
        db_path = tmppath / "sessions.db"
        self.meta_store = SessionMetaStore(db_path)
        self.manager = SessionManager(
            project_root=tmppath,
            data_dir=tmppath,
            meta_store=self.meta_store,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_send_message_adds_user_echo_to_buffer(self):
        meta = self.meta_store.create("demo", "demo title")
        client = FakeClient()
        managed = ManagedSession(
            session_id=meta.id,
            client=client,
            status="idle",
        )
        self.manager.sessions[meta.id] = managed

        async def _run():
            await self.manager.send_message(meta.id, "hello realtime")
            self.assertEqual(client.sent_queries, ["hello realtime"])
            self.assertGreaterEqual(len(managed.message_buffer), 1)
            echo = managed.message_buffer[0]
            self.assertEqual(echo.get("type"), "user")
            self.assertEqual(echo.get("content"), "hello realtime")
            self.assertEqual(echo.get("local_echo"), True)

            if managed.consumer_task:
                await managed.consumer_task

        asyncio.run(_run())

    def test_send_message_prunes_previous_stream_events(self):
        meta = self.meta_store.create("demo", "demo title")
        client = FakeClient()
        managed = ManagedSession(
            session_id=meta.id,
            client=client,
            status="idle",
            message_buffer=[
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
            ],
        )
        self.manager.sessions[meta.id] = managed

        async def _run():
            await self.manager.send_message(meta.id, "新问题")
            if managed.consumer_task:
                await managed.consumer_task

            self.assertFalse(any(msg.get("type") == "stream_event" for msg in managed.message_buffer))
            self.assertTrue(any(msg.get("type") == "assistant" for msg in managed.message_buffer))
            self.assertTrue(any(msg.get("type") == "result" for msg in managed.message_buffer))
            self.assertTrue(any(msg.get("local_echo") for msg in managed.message_buffer))

        asyncio.run(_run())

    def test_consume_result_prunes_stream_events_after_completion(self):
        meta = self.meta_store.create("demo", "demo title")
        client = FakeStreamingClient(
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
        self.manager.sessions[meta.id] = managed
        self.meta_store.update_status(meta.id, "running")

        async def _run():
            await self.manager._consume_messages(managed)
            self.assertEqual(managed.status, "completed")
            self.assertFalse(any(msg.get("type") == "stream_event" for msg in managed.message_buffer))
            self.assertTrue(any(msg.get("type") == "assistant" for msg in managed.message_buffer))
            self.assertTrue(any(msg.get("type") == "result" for msg in managed.message_buffer))

        asyncio.run(_run())

    def test_ask_user_question_waits_for_answer_and_merges_answers(self):
        if not SDK_AVAILABLE:
            self.skipTest("claude_agent_sdk is not installed")

        meta = self.meta_store.create("demo", "demo title")
        managed = ManagedSession(
            session_id=meta.id,
            client=FakeClient(),
            status="running",
        )
        self.manager.sessions[meta.id] = managed

        callback = self.manager._build_can_use_tool_callback(meta.id)

        async def _run():
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

            self.assertGreaterEqual(len(managed.message_buffer), 1)
            ask_message = managed.message_buffer[-1]
            self.assertEqual(ask_message.get("type"), "ask_user_question")
            question_id = ask_message.get("question_id")
            self.assertTrue(question_id)

            await self.manager.answer_user_question(
                session_id=meta.id,
                question_id=question_id,
                answers={"请选择时长": "2分钟"},
            )

            allow_result = await task
            self.assertEqual(
                allow_result.updated_input.get("answers", {}).get("请选择时长"),
                "2分钟",
            )

        asyncio.run(_run())

    def test_answer_user_question_raises_for_unknown_question(self):
        meta = self.meta_store.create("demo", "demo title")
        managed = ManagedSession(
            session_id=meta.id,
            client=FakeClient(),
            status="running",
        )
        self.manager.sessions[meta.id] = managed

        async def _run():
            with self.assertRaises(ValueError):
                await self.manager.answer_user_question(
                    session_id=meta.id,
                    question_id="missing-question-id",
                    answers={"Q": "A"},
                )

        asyncio.run(_run())

    def test_interrupt_session_requests_interrupt_and_keeps_consumer_alive(self):
        meta = self.meta_store.create("demo", "demo title")
        client = FakeClient()
        managed = ManagedSession(
            session_id=meta.id,
            client=client,
            sdk_session_id="sdk-123",
            status="running",
        )
        self.manager.sessions[meta.id] = managed
        self.meta_store.update_status(meta.id, "running")

        async def _run():
            new_status = await self.manager.interrupt_session(meta.id)
            self.assertEqual(new_status, "running")
            self.assertTrue(client.interrupted)
            self.assertEqual(managed.status, "running")
            self.assertEqual(managed.interrupt_requested, True)
            self.assertEqual(len(managed.message_buffer), 0)
            stored = self.meta_store.get(meta.id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.status, "running")

        asyncio.run(_run())

    def test_resolve_result_status_returns_interrupted_when_interrupt_requested(self):
        result = {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "stop_reason": None,
        }
        resolved = self.manager._resolve_result_status(
            result,
            interrupt_requested=True,
        )
        self.assertEqual(resolved, "interrupted")


if __name__ == "__main__":
    unittest.main()
