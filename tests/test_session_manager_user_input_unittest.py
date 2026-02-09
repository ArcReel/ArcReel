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

    async def query(self, content: str) -> None:
        self.sent_queries.append(content)

    async def receive_messages(self):
        if False:
            yield None


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


if __name__ == "__main__":
    unittest.main()
