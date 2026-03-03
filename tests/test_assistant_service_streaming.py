"""Unit tests for AssistantService streaming snapshot/replay behavior."""

import pytest
from pathlib import Path

from fastapi.sse import ServerSentEvent

from server.agent_runtime.models import SessionMeta
from server.agent_runtime.service import AssistantService
from tests.factories import make_session_meta

import asyncio


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


def _parse_sse_event(sse_event: ServerSentEvent) -> tuple[str, dict]:
    event_name = sse_event.event or ""
    payload = sse_event.data
    if not isinstance(payload, dict):
        payload = {}
    return event_name, payload


class TestAssistantServiceStreaming:
    async def test_stream_subscribes_before_snapshot_and_uses_replay(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()

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

        stream = service.stream_events("session-1")
        first_event = await anext(stream)
        event_name, payload = _parse_sse_event(first_event)
        assert event_name == "snapshot"
        assert payload["turns"][0]["type"] == "user"
        await stream.aclose()

        subscribe_idx = call_log.index(("subscribe", "session-1", True))
        read_raw_idx = call_log.index(
            ("read_raw_messages", "session-1", "sdk-1", "demo")
        )
        assert subscribe_idx < read_raw_idx

    async def test_stream_replay_overflow_closes_stream_immediately(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()

        call_log: list[tuple] = []
        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader(call_log, history_raw=[])
        service.session_manager = _FakeSessionManager(
            call_log,
            status="running",
            replay_messages=[{"type": "_queue_overflow", "session_id": "sdk-1"}],
        )

        stream = service.stream_events("session-1")
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        await stream.aclose()

        assert ("subscribe", "session-1", True) in call_log
        assert ("unsubscribe", "session-1") in call_log
        assert ("read_raw_messages", "session-1", "sdk-1", "demo") not in call_log

    async def test_stream_emits_delta_patch_question_and_status_events(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()

        call_log: list[tuple] = []
        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader(call_log, history_raw=[])
        fake_manager = _FakeSessionManager(call_log, status="running", replay_messages=[])
        service.session_manager = fake_manager

        stream = service.stream_events("session-1")
        snapshot_event = await anext(stream)
        snapshot_name, snapshot_payload = _parse_sse_event(snapshot_event)
        assert snapshot_name == "snapshot"
        assert snapshot_payload.get("turns") == []

        queue = fake_manager.last_queue
        assert queue is not None

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

        event_names = [name for name, _ in events]

        assert "delta" in event_names
        assert "patch" in event_names
        assert "question" in event_names
        assert "status" in event_names
        assert "message" not in event_names
        assert "turn_snapshot" not in event_names
        assert "turn_patch" not in event_names

        delta_payload = next(payload for name, payload in events if name == "delta")
        assert delta_payload.get("delta_type") == "text_delta"
        assert delta_payload.get("text") == "Hi"
        assert isinstance(delta_payload.get("draft_turn"), dict)
        assert delta_payload.get("session_id") == "session-1"
        assert delta_payload.get("sdk_session_id") == "sdk-1"

        patch_payload = next(payload for name, payload in events if name == "patch")
        assert patch_payload.get("session_id") == "session-1"
        assert patch_payload.get("sdk_session_id") == "sdk-1"

        question_payload = next(payload for name, payload in events if name == "question")
        assert question_payload.get("session_id") == "session-1"
        assert question_payload.get("sdk_session_id") == "sdk-1"

        status_payload = next(payload for name, payload in events if name == "status")
        assert status_payload.get("status") == "completed"
        assert status_payload.get("subtype") == "success"
        assert status_payload.get("stop_reason") == "end_turn"
        assert status_payload.get("is_error") == False
        assert status_payload.get("session_id") == "session-1"
        assert status_payload.get("sdk_session_id") == "sdk-1"

    async def test_stream_completed_session_emits_snapshot_and_status(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(status="completed")

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

        stream = service.stream_events("session-1")
        first = await anext(stream)
        second = await anext(stream)
        await stream.aclose()

        first_name, first_payload = _parse_sse_event(first)
        second_name, second_payload = _parse_sse_event(second)

        assert first_name == "snapshot"
        assert len(first_payload.get("turns", [])) == 3
        assert first_payload.get("session_id") == "session-1"
        assert first_payload.get("sdk_session_id") == "sdk-1"
        assert second_name == "status"
        assert second_payload.get("status") == "completed"
        assert second_payload.get("subtype") == "success"
        assert second_payload.get("stop_reason") == "end_turn"
        assert second_payload.get("is_error") == False
        assert second_payload.get("session_id") == "session-1"
        assert second_payload.get("sdk_session_id") == "sdk-1"

    async def test_stream_runtime_status_emits_interrupted_status(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()

        call_log: list[tuple] = []
        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader(call_log, history_raw=[])
        fake_manager = _FakeSessionManager(call_log, status="running", replay_messages=[])
        service.session_manager = fake_manager

        stream = service.stream_events("session-1")
        snapshot_event = await anext(stream)
        snapshot_name, _ = _parse_sse_event(snapshot_event)
        assert snapshot_name == "snapshot"

        queue = fake_manager.last_queue
        assert queue is not None
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

        event_name, payload = _parse_sse_event(status_event)
        assert event_name == "status"
        assert payload.get("status") == "interrupted"
        assert payload.get("subtype") == "interrupted"
        assert payload.get("is_error") == False
        assert payload.get("session_id") == "session-1"
        assert payload.get("sdk_session_id") == "sdk-1"

    async def test_stream_result_prefers_session_status_from_result_message(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()

        call_log: list[tuple] = []
        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader(call_log, history_raw=[])
        fake_manager = _FakeSessionManager(call_log, status="running", replay_messages=[])
        service.session_manager = fake_manager

        stream = service.stream_events("session-1")
        snapshot_event = await anext(stream)
        snapshot_name, _ = _parse_sse_event(snapshot_event)
        assert snapshot_name == "snapshot"

        queue = fake_manager.last_queue
        assert queue is not None
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

        event_name, payload = status_event
        assert event_name == "status"
        assert payload.get("status") == "interrupted"
        assert payload.get("subtype") == "error_during_execution"
        assert payload.get("is_error") == True
        assert payload.get("session_id") == "session-1"
        assert payload.get("sdk_session_id") == "sdk-1"

    async def test_build_projector_dedupes_local_echo_when_transcript_has_real_user(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()
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

        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader([], history_raw=history)
        service.session_manager = _FakeSessionManager([], status="running", replay_messages=buffer)

        projector = service._build_projector(meta, "session-1")
        # local echo should be dropped, so only the real transcript user turn exists
        assert len(projector.turns) == 1
        assert projector.turns[0]["uuid"] == "real-1"

    async def test_build_projector_keeps_new_local_echo_for_old_same_text(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()
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

        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader([], history_raw=history)
        service.session_manager = _FakeSessionManager([], status="running", replay_messages=buffer)

        projector = service._build_projector(meta, "session-1")
        # Time gap is > 5 seconds, so it's treated as a new message
        assert len(projector.turns) == 2
        assert projector.turns[-1]["uuid"] == "local-user-new"

    def test_prune_transient_buffer_removes_groupable_messages(self):
        """Verify _prune_transient_buffer clears user/assistant/result messages
        in addition to stream_event and runtime_status."""
        from server.agent_runtime.session_manager import (
            ManagedSession,
            SessionManager,
        )

        buffer = [
            {"type": "user", "content": "Q1", "uuid": "u1", "local_echo": True},
            {"type": "stream_event", "event": {"type": "text_delta"}},
            {"type": "assistant", "content": [{"type": "text", "text": "A1"}]},
            {"type": "result", "subtype": "success"},
            {"type": "runtime_status", "status": "completed"},
            {"type": "ask_user_question", "question_id": "aq-1", "questions": []},
        ]
        managed = ManagedSession.__new__(ManagedSession)
        managed.message_buffer = list(buffer)

        SessionManager._prune_transient_buffer(managed)

        remaining_types = [m.get("type") for m in managed.message_buffer]
        assert remaining_types == ["ask_user_question"]

    async def test_get_snapshot_no_duplicate_during_streaming(self, tmp_path):
        """During streaming, buffer contains assistant messages without uuid while
        transcript already has the same messages with uuid.  get_snapshot must
        not produce duplicate turns."""
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()

        call_log: list[tuple] = []
        # Transcript already has current round's user + assistant (CLI wrote them)
        history = [
            {
                "type": "user",
                "content": "Q1",
                "uuid": "user-1",
                "timestamp": "2026-02-09T08:00:01Z",
            },
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "A1 - first answer"}],
                "uuid": "assistant-1",
                "timestamp": "2026-02-09T08:00:02Z",
            },
        ]
        # Buffer also has the same messages but without uuid (SDK objects).
        # SDK content blocks lack the "type" field that the CLI adds.
        stale_buffer = [
            {
                "type": "user",
                "content": "Q1",
                "uuid": "local-user-abc",
                "local_echo": True,
                "timestamp": "2026-02-09T08:00:00Z",
            },
            {
                "type": "assistant",
                "content": [{"text": "A1 - first answer"}],
                # No uuid — SDK AssistantMessage doesn't have one
                # No "type" in content block — SDK dataclass omits it
            },
            {
                "type": "stream_event",
                "event": {"type": "content_block_delta"},
            },
        ]
        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader(call_log, history_raw=history)
        service.session_manager = _FakeSessionManager(
            call_log,
            status="running",
            replay_messages=stale_buffer,
        )

        payload = await service.get_snapshot("session-1")
        turns = payload.get("turns", [])
        turn_types = [t.get("type") for t in turns]
        # Should be exactly 2 turns: user + assistant, no duplicates
        assert turn_types == ["user", "assistant"]
        assistant_turn = turns[-1]
        assert len(assistant_turn.get("content", [])) == 1

    async def test_get_snapshot_no_duplicate_with_tool_use_during_streaming(self, tmp_path):
        """Buffer assistant content blocks lack the 'type' field that the CLI
        transcript includes.  content_key must normalise across both formats."""
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()

        call_log: list[tuple] = []
        # Transcript content blocks include "type"
        history = [
            {
                "type": "user",
                "content": "run ls",
                "uuid": "user-1",
                "timestamp": "2026-02-09T08:00:01Z",
            },
            {
                "type": "assistant",
                "content": [
                    {"type": "text", "text": "Let me run that."},
                    {"type": "tool_use", "id": "tool-1", "name": "Bash",
                     "input": {"command": "ls"}},
                ],
                "uuid": "assistant-1",
                "timestamp": "2026-02-09T08:00:02Z",
            },
        ]
        # Buffer content blocks omit "type" (SDK dataclass serialization)
        stale_buffer = [
            {
                "type": "assistant",
                "content": [
                    {"text": "Let me run that."},
                    {"id": "tool-1", "name": "Bash",
                     "input": {"command": "ls"}},
                ],
            },
        ]
        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader(call_log, history_raw=history)
        service.session_manager = _FakeSessionManager(
            call_log,
            status="running",
            replay_messages=stale_buffer,
        )

        payload = await service.get_snapshot("session-1")
        turns = payload.get("turns", [])
        turn_types = [t.get("type") for t in turns]
        assert turn_types == ["user", "assistant"]
        assistant_turn = turns[-1]
        # Should have exactly 2 content blocks, not 4
        assert len(assistant_turn.get("content", [])) == 2

    async def test_get_snapshot_preserves_user_between_rounds_during_streaming(self, tmp_path):
        """When streaming round 3, buffer has local_echo user-Q3 and assistant-A3
        without uuid.  Transcript has rounds 1-2 complete + user-Q3.  The snapshot
        must keep user-Q3 between assistant-A2 and assistant-A3 so the turns are
        not merged."""
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()

        call_log: list[tuple] = []
        # Transcript: rounds 1+2 complete, round 3 user written
        history = [
            {"type": "user", "content": "Q1", "uuid": "u1",
             "timestamp": "2026-02-09T08:00:01Z"},
            {"type": "assistant",
             "content": [{"type": "text", "text": "A1"}],
             "uuid": "a1", "timestamp": "2026-02-09T08:00:02Z"},
            {"type": "result", "subtype": "success", "uuid": "r1",
             "timestamp": "2026-02-09T08:00:03Z"},
            {"type": "user", "content": "Q2", "uuid": "u2",
             "timestamp": "2026-02-09T08:00:10Z"},
            {"type": "assistant",
             "content": [{"type": "text", "text": "A2"}],
             "uuid": "a2", "timestamp": "2026-02-09T08:00:11Z"},
            {"type": "result", "subtype": "success", "uuid": "r2",
             "timestamp": "2026-02-09T08:00:12Z"},
            {"type": "user", "content": "Q3", "uuid": "u3",
             "timestamp": "2026-02-09T08:00:20Z"},
        ]
        # Buffer after prune: local_echo user-Q3 + assistant-A3 (no uuid)
        buffer = [
            {"type": "user", "content": "Q3",
             "uuid": "local-user-q3", "local_echo": True,
             "timestamp": "2026-02-09T08:00:19Z"},
            {"type": "assistant",
             "content": [{"text": "A3 - new answer"}]},
            {"type": "stream_event",
             "event": {"type": "content_block_delta"}},
        ]
        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader(
            call_log, history_raw=history)
        service.session_manager = _FakeSessionManager(
            call_log, status="running", replay_messages=buffer)

        payload = await service.get_snapshot("session-1")
        turns = payload.get("turns", [])
        turn_types = [t.get("type") for t in turns]
        # Transcript provides all 3 users and 2 assistants + 2 results.
        # Buffer assistant-A3 (no uuid) is now correctly included — it
        # represents the latest reply not yet persisted to JSONL.
        # Content-based dedup prevents genuine duplicates.
        # user-Q3 must be present after result-R2 so A2 and A3 are not merged.
        assert turn_types == [
            "user", "assistant", "result", "user", "assistant", "result", "user", "assistant",
        ], f"unexpected turns={turn_types}"

    async def test_stream_new_session_first_round_preserves_user(self, tmp_path):
        """First round of a brand new session: transcript is empty, buffer has
        only local_echo user.  The stream snapshot must include the user turn."""
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(
            id="session-new",
            sdk_session_id=None,
            title="new chat",
            created_at="2026-02-10T08:00:00Z",
            updated_at="2026-02-10T08:00:00Z",
        )

        call_log: list[tuple] = []
        # Buffer: only local_echo user (SDK hasn't returned anything yet)
        buffer = [
            {"type": "user", "content": "Hello",
             "uuid": "local-user-first", "local_echo": True,
             "timestamp": "2026-02-10T08:00:01Z"},
        ]
        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader(
            call_log, history_raw=[])
        service.session_manager = _FakeSessionManager(
            call_log, status="running", replay_messages=buffer)

        stream = service.stream_events("session-new")
        first_event = await anext(stream)
        event_name, payload = _parse_sse_event(first_event)
        assert event_name == "snapshot"
        turns = payload.get("turns", [])
        assert len(turns) >= 1, f"expected at least 1 turn, got {turns}"
        assert turns[0]["type"] == "user"
        await stream.aclose()

    async def test_get_snapshot_no_duplicate_turns_across_rounds(self, tmp_path):
        """After _prune_transient_buffer clears groupable messages, get_snapshot
        should produce clean turns from transcript alone, with no duplicates."""
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(status="completed")

        call_log: list[tuple] = []
        # Transcript has two complete rounds
        history = [
            {
                "type": "user",
                "content": "Q1",
                "uuid": "user-1",
                "timestamp": "2026-02-09T08:00:01Z",
            },
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "A1 - skills list"}],
                "uuid": "assistant-1",
                "timestamp": "2026-02-09T08:00:02Z",
            },
            {
                "type": "user",
                "content": "Q2",
                "uuid": "user-2",
                "timestamp": "2026-02-09T08:00:03Z",
            },
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "A2 - cwd answer"}],
                "uuid": "assistant-2",
                "timestamp": "2026-02-09T08:00:04Z",
            },
        ]
        # Buffer is empty after prune (groupable messages cleared).
        # Only non-groupable messages like ask_user_question would remain.
        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader(call_log, history_raw=history)
        service.session_manager = _FakeSessionManager(
            call_log,
            status="completed",
            replay_messages=[],  # buffer pruned
        )

        payload = await service.get_snapshot("session-1")
        turns = payload.get("turns", [])
        turn_types = [t.get("type") for t in turns]
        assert turn_types == ["user", "assistant", "user", "assistant"]
        last_assistant = turns[-1]
        assert last_assistant.get("uuid") == "assistant-2"
        assert len(last_assistant.get("content", [])) == 1
        assert last_assistant["content"][0].get("text") == "A2 - cwd answer"

    async def test_build_projector_preserves_repeated_assistant_replies_across_rounds(self, tmp_path):
        """Verify that identical assistant replies in different rounds (e.g. 'Done')
        are not deduplicated away when processing the buffer, because a new user message
        clears the content-based dedup cache."""
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()
        
        # Round 1 in transcript: Assistant said "Done"
        history = [
            {
                "type": "user",
                "content": "task 1",
                "uuid": "u1",
                "timestamp": "2026-02-09T08:00:00Z",
            },
            {
                "type": "assistant",
                "content": [{"text": "Done"}],
                "uuid": "a1",
                "timestamp": "2026-02-09T08:00:05Z",
            }
        ]
        
        # Round 2 in buffer: User asks task 2, Assistant also says "Done" (no uuid from SDK)
        buffer = [
            {
                "type": "user",
                "content": "task 2",
                "uuid": "u2",
                "timestamp": "2026-02-09T08:00:10Z",
            },
            {
                "type": "assistant",
                "content": [{"text": "Done"}],
                # No uuid, mimicking SDK payload
            }
        ]

        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader([], history_raw=history)
        service.session_manager = _FakeSessionManager([], status="running", replay_messages=buffer)

        projector = service._build_projector(meta, "session-1")
        
        # We should have 4 turns total: user1, asst1, user2, asst2
        assert len(projector.turns) == 4
        assert projector.turns[0]["content"][0]["text"] == "task 1"
        assert projector.turns[1]["content"][0]["text"] == "Done"
        assert projector.turns[2]["content"][0]["text"] == "task 2"
        assert projector.turns[3]["content"][0]["text"] == "Done"

    async def test_build_projector_dedupes_result_messages(self, tmp_path):
        """Verify that buffer result messages (lacking timestamp/uuid) are
        successfully deduplicated against transcript result messages in the same round."""
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()
        
        # Round 1 in transcript: has a completed result with timestamp
        history = [
            {
                "type": "user",
                "content": "task 1",
                "uuid": "u1",
            },
            {
                "type": "assistant",
                "content": [{"text": "Done"}],
                "uuid": "a1",
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "uuid": "r1",
                "timestamp": "2026-02-09T08:00:05Z",
            }
        ]
        
        # Buffer has the same result message but lacks uuid and timestamp (SDK format)
        buffer = [
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
            }
        ]

        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader([], history_raw=history)
        service.session_manager = _FakeSessionManager([], status="completed", replay_messages=buffer)

        projector = service._build_projector(meta, "session-1")
        
        # We should have exactly 3 turns total: user, assistant, result.
        # The buffer result should be deduplicated away.
        assert len(projector.turns) == 3
        turn_types = [t.get("type") for t in projector.turns]
        assert turn_types == ["user", "assistant", "result"]

    async def test_build_projector_ignores_system_user_when_scoping_dedup(self, tmp_path):
        """Verify that system-injected user messages do not reset the content deduplication
        scope. The scope should only begin at the last REAL user message."""
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta()
        
        # Transcript: User asks question, Assistant uses tool, Subagent returns result
        history = [
            {
                "type": "user",
                "content": "task",
                "uuid": "u1",
            },
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Task", "input": {}}
                ],
                "uuid": "a1",
            },
            {
                "type": "user",
                "content": "some system result",
                "uuid": "sys-u1",
                # This is the subagent metadata that identifies it as system-injected
                "sourceToolAssistantUUID": "agent-123",
            }
        ]
        
        # Buffer: The same assistant tool_use message (replayed by SDK, no uuid).
        # It must be correctly deduplicated against a1.
        buffer = [
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Task", "input": {}}
                ],
            }
        ]

        service.meta_store = _FakeMetaStore(meta)
        service.transcript_reader = _FakeTranscriptReader([], history_raw=history)
        service.session_manager = _FakeSessionManager([], status="running", replay_messages=buffer)

        projector = service._build_projector(meta, "session-1")
        
        # We should have exactly 2 turns total!
        # turn 1: user "task"
        # turn 2: assistant tool_use + system result folded in
        # The buffer assistant message must be completely deduplicated away.
        assert len(projector.turns) == 2
        assert projector.turns[0]["type"] == "user"
        assert projector.turns[1]["type"] == "assistant"
