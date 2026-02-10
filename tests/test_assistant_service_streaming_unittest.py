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


class _FakeSessionManager:
    def __init__(
        self,
        call_log: list[tuple],
        status: str = "running",
        replay_messages: list[dict] | None = None,
        pending_questions: list[dict] | None = None,
    ):
        self.call_log = call_log
        self.status = status
        self.replay_messages = replay_messages or []
        self.pending_questions = pending_questions or []
        self.last_queue: asyncio.Queue | None = None

    def get_status(self, session_id: str):
        self.call_log.append(("get_status", session_id))
        return self.status

    def get_buffered_messages(self, session_id: str):
        self.call_log.append(("get_buffered_messages", session_id))
        return list(self.replay_messages)

    async def subscribe(self, session_id: str, replay_buffer: bool = True):
        self.call_log.append(("subscribe", session_id, replay_buffer))
        queue: asyncio.Queue = asyncio.Queue()
        for message in self.replay_messages:
            queue.put_nowait(message)
        self.last_queue = queue
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue):
        self.call_log.append(("unsubscribe", session_id))

    async def get_pending_questions_snapshot(self, session_id: str):
        self.call_log.append(("get_pending_questions_snapshot", session_id))
        return list(self.pending_questions)


def _parse_sse_event(sse_event: str) -> tuple[str, dict]:
    event_name = ""
    payload = {}
    for line in sse_event.splitlines():
        if line.startswith("event: "):
            event_name = line[len("event: "):].strip()
        elif line.startswith("data: "):
            payload = json.loads(line[len("data: "):])
    return event_name, payload


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
            service.session_manager = _FakeSessionManager(
                call_log,
                status="running",
                replay_messages=replayed,
            )

            async def _run():
                stream = service.stream_events("session-1")
                first_event = await anext(stream)
                event_name, payload = _parse_sse_event(first_event)
                self.assertEqual(event_name, "snapshot")
                self.assertEqual(payload["turns"][0]["type"], "user")
                await stream.aclose()

            asyncio.run(_run())

            subscribe_idx = call_log.index(("subscribe", "session-1", True))
            read_raw_idx = call_log.index(
                ("read_raw_messages", "session-1", "sdk-1", "demo")
            )
            self.assertLess(subscribe_idx, read_raw_idx)

    def test_stream_emits_delta_patch_question_and_status_events(self):
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
            service.meta_store = _FakeMetaStore(meta)
            service.transcript_reader = _FakeTranscriptReader(call_log, history_raw=[])
            fake_manager = _FakeSessionManager(call_log, status="running", replay_messages=[])
            service.session_manager = fake_manager

            async def _run():
                stream = service.stream_events("session-1")
                snapshot_event = await anext(stream)
                snapshot_name, snapshot_payload = _parse_sse_event(snapshot_event)
                self.assertEqual(snapshot_name, "snapshot")
                self.assertEqual(snapshot_payload.get("turns"), [])

                queue = fake_manager.last_queue
                self.assertIsNotNone(queue)

                queue.put_nowait(
                    {
                        "type": "stream_event",
                        "session_id": "sdk-1",
                        "event": {"type": "message_start"},
                    }
                )
                queue.put_nowait(
                    {
                        "type": "stream_event",
                        "session_id": "sdk-1",
                        "event": {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        },
                    }
                )
                queue.put_nowait(
                    {
                        "type": "stream_event",
                        "session_id": "sdk-1",
                        "event": {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "Hi"},
                        },
                    }
                )
                queue.put_nowait(
                    {
                        "type": "ask_user_question",
                        "question_id": "aq-1",
                        "questions": [
                            {
                                "header": "风格",
                                "question": "选择一种风格",
                                "options": [{"label": "悬疑", "description": "更紧张"}],
                            }
                        ],
                    }
                )
                queue.put_nowait(
                    {
                        "type": "assistant",
                        "content": [{"type": "text", "text": "Hi"}],
                        "uuid": "assistant-1",
                        "timestamp": "2026-02-09T08:00:03Z",
                    }
                )
                queue.put_nowait(
                    {
                        "type": "result",
                        "subtype": "success",
                        "stop_reason": "end_turn",
                        "is_error": False,
                        "session_id": "sdk-1",
                        "uuid": "result-1",
                        "timestamp": "2026-02-09T08:00:04Z",
                    }
                )

                events: list[tuple[str, dict]] = []
                while True:
                    chunk = await anext(stream)
                    event_name, payload = _parse_sse_event(chunk)
                    if not event_name:
                        continue
                    events.append((event_name, payload))
                    if event_name == "status":
                        break

                await stream.aclose()
                return events

            events = asyncio.run(_run())
            event_names = [name for name, _ in events]

            self.assertIn("delta", event_names)
            self.assertIn("patch", event_names)
            self.assertIn("question", event_names)
            self.assertIn("status", event_names)
            self.assertNotIn("message", event_names)
            self.assertNotIn("turn_snapshot", event_names)
            self.assertNotIn("turn_patch", event_names)

            delta_payload = next(payload for name, payload in events if name == "delta")
            self.assertEqual(delta_payload.get("delta_type"), "text_delta")
            self.assertEqual(delta_payload.get("text"), "Hi")
            self.assertIsInstance(delta_payload.get("draft_turn"), dict)

            status_payload = next(payload for name, payload in events if name == "status")
            self.assertEqual(status_payload.get("status"), "completed")
            self.assertEqual(status_payload.get("subtype"), "success")
            self.assertEqual(status_payload.get("stop_reason"), "end_turn")
            self.assertEqual(status_payload.get("is_error"), False)
            self.assertEqual(status_payload.get("session_id"), "sdk-1")

    def test_stream_completed_session_emits_snapshot_and_status(self):
        with TemporaryDirectory() as tmpdir:
            service = AssistantService(project_root=Path(tmpdir))
            meta = SessionMeta(
                id="session-1",
                sdk_session_id="sdk-1",
                project_name="demo",
                title="demo",
                status="completed",
                transcript_path=None,
                created_at="2026-02-09T08:00:00Z",
                updated_at="2026-02-09T08:00:00Z",
            )

            call_log: list[tuple] = []
            history = [
                {
                    "type": "user",
                    "content": "hello",
                    "uuid": "user-1",
                    "timestamp": "2026-02-09T08:00:01Z",
                },
                {
                    "type": "assistant",
                    "content": [{"type": "text", "text": "Hi"}],
                    "uuid": "assistant-1",
                    "timestamp": "2026-02-09T08:00:02Z",
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "stop_reason": "end_turn",
                    "is_error": False,
                    "session_id": "sdk-1",
                    "uuid": "result-1",
                    "timestamp": "2026-02-09T08:00:03Z",
                },
            ]

            service.meta_store = _FakeMetaStore(meta)
            service.transcript_reader = _FakeTranscriptReader(call_log, history_raw=history)
            service.session_manager = _FakeSessionManager(call_log, status="completed")

            async def _run():
                stream = service.stream_events("session-1")
                first = await anext(stream)
                second = await anext(stream)
                await stream.aclose()
                return _parse_sse_event(first), _parse_sse_event(second)

            (first_name, first_payload), (second_name, second_payload) = asyncio.run(_run())
            self.assertEqual(first_name, "snapshot")
            self.assertEqual(len(first_payload.get("turns", [])), 3)
            self.assertEqual(second_name, "status")
            self.assertEqual(second_payload.get("status"), "completed")
            self.assertEqual(second_payload.get("subtype"), "success")
            self.assertEqual(second_payload.get("stop_reason"), "end_turn")
            self.assertEqual(second_payload.get("is_error"), False)
            self.assertEqual(second_payload.get("session_id"), "sdk-1")

    def test_stream_runtime_status_emits_interrupted_status(self):
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
            service.meta_store = _FakeMetaStore(meta)
            service.transcript_reader = _FakeTranscriptReader(call_log, history_raw=[])
            fake_manager = _FakeSessionManager(call_log, status="running", replay_messages=[])
            service.session_manager = fake_manager

            async def _run():
                stream = service.stream_events("session-1")
                snapshot_event = await anext(stream)
                snapshot_name, _ = _parse_sse_event(snapshot_event)
                self.assertEqual(snapshot_name, "snapshot")

                queue = fake_manager.last_queue
                self.assertIsNotNone(queue)
                queue.put_nowait(
                    {
                        "type": "runtime_status",
                        "status": "interrupted",
                        "subtype": "interrupted",
                        "session_id": "sdk-1",
                        "is_error": False,
                    }
                )

                status_event = await anext(stream)
                await stream.aclose()
                return _parse_sse_event(status_event)

            event_name, payload = asyncio.run(_run())
            self.assertEqual(event_name, "status")
            self.assertEqual(payload.get("status"), "interrupted")
            self.assertEqual(payload.get("subtype"), "interrupted")
            self.assertEqual(payload.get("is_error"), False)
            self.assertEqual(payload.get("session_id"), "sdk-1")

    def test_stream_result_prefers_session_status_from_result_message(self):
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
            service.meta_store = _FakeMetaStore(meta)
            service.transcript_reader = _FakeTranscriptReader(call_log, history_raw=[])
            fake_manager = _FakeSessionManager(call_log, status="running", replay_messages=[])
            service.session_manager = fake_manager

            async def _run():
                stream = service.stream_events("session-1")
                snapshot_event = await anext(stream)
                snapshot_name, _ = _parse_sse_event(snapshot_event)
                self.assertEqual(snapshot_name, "snapshot")

                queue = fake_manager.last_queue
                self.assertIsNotNone(queue)
                queue.put_nowait(
                    {
                        "type": "result",
                        "session_status": "interrupted",
                        "subtype": "error_during_execution",
                        "stop_reason": None,
                        "is_error": True,
                        "session_id": "sdk-1",
                        "uuid": "result-interrupt-1",
                        "timestamp": "2026-02-09T08:00:10Z",
                    }
                )
                status_event = None
                while True:
                    event_chunk = await anext(stream)
                    event_name, payload = _parse_sse_event(event_chunk)
                    if event_name == "status":
                        status_event = (event_name, payload)
                        break
                await stream.aclose()
                return status_event

            event_name, payload = asyncio.run(_run())
            self.assertEqual(event_name, "status")
            self.assertEqual(payload.get("status"), "interrupted")
            self.assertEqual(payload.get("subtype"), "error_during_execution")
            self.assertEqual(payload.get("is_error"), True)
            self.assertEqual(payload.get("session_id"), "sdk-1")

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
