"""Unit tests for SessionManager._on_sdk_session_id_received during streaming."""

from __future__ import annotations

from contextlib import asynccontextmanager

from server.agent_runtime.session_actor import SessionActor
from server.agent_runtime.session_manager import ManagedSession
from tests.fakes import FakeSDKClient, session_state_message


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

    async def test_stream_opened_once_the_session_is_listed_subscribes_to_the_managed_channel(
        self, session_manager, meta_store, monkeypatch
    ):
        """新会话的元数据一落库就可被列出：此时打开的 entry 流挂在会话实际广播的通道上。"""
        sdk_session_id = "sdk-new-listed"
        managed = _make_managed()
        session_manager.sessions[managed.session_id] = managed
        subscribed = []
        real_create = meta_store.create

        async def _create_then_client_subscribes(project_name, session_id):
            meta = await real_create(project_name, session_id)
            subscribed.append(session_manager._subscribe(session_id)[0])
            return meta

        monkeypatch.setattr(meta_store, "create", _create_then_client_subscribes)

        await session_manager._on_sdk_session_id_received(
            managed, StreamEvent(sdk_session_id), {"session_id": sdk_session_id}
        )

        assert subscribed == [managed.channel]
        assert session_manager._channels[sdk_session_id] is managed.channel

    async def test_usage_failure_does_not_block_settling_the_turn(self, session_manager, meta_store, monkeypatch):
        meta = await meta_store.create("demo", "sdk-usage-error-789")
        managed = _make_managed(session_id=meta.id, project_name="demo", status="running")
        managed.resolved_sdk_id = meta.id
        called = False

        async def _raise_usage_error(*_args, **_kwargs):
            nonlocal called
            called = True
            raise RuntimeError("usage db unavailable")

        monkeypatch.setattr(session_manager, "_record_assistant_usage", _raise_usage_error)

        on_message = session_manager._make_actor_message_callback([managed])
        on_message({"type": "result", "subtype": "success"})
        on_message(session_state_message("idle"))
        managed._inbox.put_nowait(None)
        await session_manager._process_inbox(managed)

        assert called is True
        refreshed = await meta_store.get(meta.id)
        assert refreshed is not None
        assert refreshed.status == "completed"

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
