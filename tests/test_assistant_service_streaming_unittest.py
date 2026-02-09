"""Unit tests for AssistantService streaming snapshot/replay behavior."""

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from webui.server.agent_runtime.models import SessionMeta
from webui.server.agent_runtime.service import AssistantService


class _FakeMetaStore:
    def __init__(self, meta: SessionMeta):
        self._meta = meta

    def get(self, session_id: str):
        if session_id == self._meta.id:
            return self._meta
        return None


class _FakeTranscriptReader:
    def __init__(self, call_log: list[tuple], history_raw: list[dict] | None = None):
        self.call_log = call_log
        self.history_raw = history_raw or []

    def read_raw_messages(self, session_id: str, sdk_session_id=None, project_name=None):
        self.call_log.append(("read_raw_messages", session_id, sdk_session_id, project_name))
        return list(self.history_raw)

    def read_messages(self, session_id: str, sdk_session_id=None, project_name=None):
        self.call_log.append(("read_messages", session_id, sdk_session_id, project_name))
        return []


class _FakeSessionManager:
    def __init__(self, call_log: list[tuple], replay_messages: list[dict] | None = None):
        self.call_log = call_log
        self.replay_messages = replay_messages or []

    def get_status(self, session_id: str):
        self.call_log.append(("get_status", session_id))
        return "running"

    async def subscribe(self, session_id: str, replay_buffer: bool = True):
        self.call_log.append(("subscribe", session_id, replay_buffer))
        queue: asyncio.Queue = asyncio.Queue()
        for message in self.replay_messages:
            queue.put_nowait(message)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue):
        self.call_log.append(("unsubscribe", session_id))

    async def get_pending_questions_snapshot(self, session_id: str):
        self.call_log.append(("get_pending_questions_snapshot", session_id))
        return []


def _parse_sse_payload(sse_event: str) -> dict:
    lines = [line for line in sse_event.splitlines() if line]
    data_line = next((line for line in lines if line.startswith("data: ")), "data: {}")
    return json.loads(data_line[len("data: "):])


class TestAssistantServiceStreaming(unittest.TestCase):
    def test_stream_subscribes_before_snapshot_and_uses_replay(self):
        with TemporaryDirectory() as tmpdir:
            service = AssistantService(project_root=Path(tmpdir))
            meta = SessionMeta(
                id="session-1",
                sdk_session_id="sdk-1",
                project_name="demo",
                title="demo",
                status="running",
                transcript_path=None,
                created_at="2026-02-09T08:00:00Z",
                updated_at="2026-02-09T08:00:00Z",
            )

            call_log: list[tuple] = []
            replayed = [
                {
                    "type": "user",
                    "content": "hello",
                    "uuid": "local-user-1",
                    "local_echo": True,
                    "timestamp": "2026-02-09T08:00:01Z",
                }
            ]
            service.meta_store = _FakeMetaStore(meta)
            service.transcript_reader = _FakeTranscriptReader(call_log, history_raw=[])
            service.session_manager = _FakeSessionManager(call_log, replay_messages=replayed)

            async def _run():
                stream = service.stream_events("session-1")
                first_event = await anext(stream)
                payload = _parse_sse_payload(first_event)
                self.assertEqual(payload["turns"][0]["type"], "user")
                await stream.aclose()

            asyncio.run(_run())

            subscribe_idx = call_log.index(("subscribe", "session-1", True))
            read_raw_idx = call_log.index(
                ("read_raw_messages", "session-1", "sdk-1", "demo")
            )
            self.assertLess(subscribe_idx, read_raw_idx)

    def test_merge_raw_messages_dedupes_local_echo_when_transcript_has_real_user(self):
        with TemporaryDirectory() as tmpdir:
            service = AssistantService(project_root=Path(tmpdir))
            history = [
                {
                    "type": "user",
                    "content": "hello",
                    "uuid": "real-1",
                    "timestamp": "2026-02-09T08:00:02Z",
                }
            ]
            buffer = [
                {
                    "type": "user",
                    "content": "hello",
                    "uuid": "local-user-1",
                    "local_echo": True,
                    "timestamp": "2026-02-09T08:00:01Z",
                }
            ]

            merged = service._merge_raw_messages(history, buffer)
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["uuid"], "real-1")

    def test_merge_raw_messages_keeps_new_local_echo_for_old_same_text(self):
        with TemporaryDirectory() as tmpdir:
            service = AssistantService(project_root=Path(tmpdir))
            history = [
                {
                    "type": "user",
                    "content": "hello",
                    "uuid": "real-old",
                    "timestamp": "2026-02-09T07:00:00Z",
                }
            ]
            buffer = [
                {
                    "type": "user",
                    "content": "hello",
                    "uuid": "local-user-new",
                    "local_echo": True,
                    "timestamp": "2026-02-09T08:00:00Z",
                }
            ]

            merged = service._merge_raw_messages(history, buffer)
            self.assertEqual(len(merged), 2)
            self.assertEqual(merged[-1]["uuid"], "local-user-new")


if __name__ == "__main__":
    unittest.main()
