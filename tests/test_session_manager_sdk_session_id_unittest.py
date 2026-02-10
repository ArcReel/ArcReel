"""Unit tests for SessionManager SDK session id updates during streaming."""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from webui.server.agent_runtime.session_manager import ManagedSession, SessionManager
from webui.server.agent_runtime.session_store import SessionMetaStore


class StreamEvent:
    def __init__(self, session_id: str, uuid: str = "stream-1"):
        self.uuid = uuid
        self.session_id = session_id
        self.event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}}
        self.parent_tool_use_id = None


class ResultMessage:
    def __init__(self, session_id: str, subtype: str = "success"):
        self.subtype = subtype
        self.duration_ms = 1
        self.duration_api_ms = 1
        self.is_error = subtype == "error"
        self.num_turns = 1
        self.session_id = session_id
        self.total_cost_usd = None
        self.usage = None
        self.result = None
        self.structured_output = None


class FakeClient:
    def __init__(self, messages):
        self._messages = messages

    async def receive_response(self):
        for message in self._messages:
            yield message


class TestSessionManagerSdkSessionId(unittest.TestCase):
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

    def test_updates_sdk_session_id_before_result(self):
        meta = self.meta_store.create("demo", "demo title")
        sdk_session_id = "sdk-early-123"
        client = FakeClient([StreamEvent(sdk_session_id), ResultMessage(sdk_session_id, "success")])
        managed = ManagedSession(
            session_id=meta.id,
            client=client,
            sdk_session_id=None,
            status="running",
        )
        self.manager.sessions[meta.id] = managed

        asyncio.run(self.manager._consume_messages(managed))

        updated_meta = self.meta_store.get(meta.id)
        self.assertIsNotNone(updated_meta)
        self.assertEqual(managed.sdk_session_id, sdk_session_id)
        self.assertEqual(updated_meta.sdk_session_id, sdk_session_id)
        self.assertEqual(managed.status, "completed")
        self.assertEqual(updated_meta.status, "completed")


if __name__ == "__main__":
    unittest.main()
