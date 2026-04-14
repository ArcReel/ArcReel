"""Unit tests for SessionManager._on_sdk_session_id_received during streaming."""

from __future__ import annotations

from contextlib import asynccontextmanager

from server.agent_runtime.session_actor import SessionActor
from server.agent_runtime.session_manager import ManagedSession
from tests.fakes import FakeSDKClient


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


def _make_managed(**overrides) -> ManagedSession:
    """Construct a ManagedSession with a dummy actor that is never started."""
    dummy_client = FakeSDKClient()

    @asynccontextmanager
    async def _factory():
        async with dummy_client as c:
            yield c

    actor = SessionActor(client_factory=_factory, on_message=lambda msg: None)
    kwargs = {
        "session_id": "temp-id",
        "actor": actor,
        "status": "running",
        "project_name": "demo",
    }
    kwargs.update(overrides)
    return ManagedSession(**kwargs)


class TestSessionManagerSdkSessionId:
    async def test_on_sdk_session_id_received_creates_db_record(self, session_manager, meta_store):
        """For new sessions, _on_sdk_session_id_received creates DB record and signals event."""
        sdk_session_id = "sdk-new-123"
        managed = _make_managed()

        await session_manager._on_sdk_session_id_received(
            managed, StreamEvent(sdk_session_id), {"session_id": sdk_session_id}
        )

        assert managed.resolved_sdk_id == sdk_session_id
        assert managed.sdk_id_event.is_set()
        # DB record should exist
        meta = await meta_store.get(sdk_session_id)
        assert meta is not None
        assert meta.project_name == "demo"
        assert meta.status == "running"

    async def test_on_sdk_session_id_received_noop_when_already_registered(self, session_manager, meta_store):
        """For sessions with resolved_sdk_id already set, it's a no-op."""
        managed = _make_managed(session_id="sdk-existing", resolved_sdk_id="sdk-existing")
        managed.sdk_id_event.set()

        await session_manager._on_sdk_session_id_received(
            managed, StreamEvent("sdk-existing"), {"session_id": "sdk-existing"}
        )
        # Should not create duplicate DB record
        meta = await meta_store.get("sdk-existing")
        assert meta is None  # No DB record was created

    async def test_process_inbox_triggers_on_sdk_session_id_received(self, session_manager, meta_store):
        """_process_inbox drains messages and calls _on_sdk_session_id_received + _finalize_turn."""
        sdk_session_id = "sdk-consume-456"
        managed = _make_managed(session_id=sdk_session_id)
        session_manager.sessions[sdk_session_id] = managed

        # Push stream event dict + result dict onto the inbox (mimicking on_actor_message).
        managed._inbox.put_nowait({"type": "stream_event", "session_id": sdk_session_id, "uuid": "u1"})
        managed._inbox.put_nowait(
            {
                "type": "result",
                "subtype": "success",
                "session_id": sdk_session_id,
                "duration_ms": 1,
                "duration_api_ms": 1,
                "is_error": False,
                "num_turns": 1,
            }
        )
        managed._inbox.put_nowait(None)  # sentinel to end processing

        await session_manager._process_inbox(managed)

        assert managed.resolved_sdk_id == sdk_session_id
        assert managed.sdk_id_event.is_set()
        # DB record should have been created by _on_sdk_session_id_received
        meta = await meta_store.get(sdk_session_id)
        assert meta is not None
        assert meta.project_name == "demo"
