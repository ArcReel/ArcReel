"""Unit tests for SessionManager._on_sdk_session_id_received during streaming."""

from server.agent_runtime.session_manager import ManagedSession, SessionManager


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


class TestSessionManagerSdkSessionId:
    async def test_on_sdk_session_id_received_creates_db_record(self, session_manager, meta_store):
        """For new sessions, _on_sdk_session_id_received creates DB record and signals event."""
        sdk_session_id = "sdk-new-123"
        managed = ManagedSession(
            session_id="temp-id",
            client=FakeClient([]),
            status="running",
            project_name="demo",
        )

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
        managed = ManagedSession(
            session_id="sdk-existing",
            client=FakeClient([]),
            status="running",
            project_name="demo",
            resolved_sdk_id="sdk-existing",
        )
        managed.sdk_id_event.set()

        await session_manager._on_sdk_session_id_received(
            managed, StreamEvent("sdk-existing"), {"session_id": "sdk-existing"}
        )
        # Should not create duplicate DB record
        meta = await meta_store.get("sdk-existing")
        assert meta is None  # No DB record was created

    async def test_consume_messages_triggers_on_sdk_session_id_received(self, session_manager, meta_store):
        """_consume_messages calls _on_sdk_session_id_received and creates DB record for new sessions."""
        sdk_session_id = "sdk-consume-456"
        client = FakeClient([StreamEvent(sdk_session_id), ResultMessage(sdk_session_id, "success")])
        managed = ManagedSession(
            session_id=sdk_session_id,  # 模拟 send_new_session 已将 temp_id 替换为 sdk_id
            client=client,
            status="running",
            project_name="demo",
        )
        session_manager.sessions[sdk_session_id] = managed

        await session_manager._consume_messages(managed)

        assert managed.resolved_sdk_id == sdk_session_id
        assert managed.sdk_id_event.is_set()
        assert managed.status == "completed"
        # DB record should have been created by _on_sdk_session_id_received
        meta = await meta_store.get(sdk_session_id)
        assert meta is not None
        assert meta.project_name == "demo"
        assert meta.status == "completed"
