"""Unit tests for SessionManager SDK session id updates during streaming."""

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
    async def test_updates_sdk_session_id_before_result(self, session_manager, meta_store):
        meta = await meta_store.create("demo", "demo title")
        sdk_session_id = "sdk-early-123"
        client = FakeClient([StreamEvent(sdk_session_id), ResultMessage(sdk_session_id, "success")])
        managed = ManagedSession(
            session_id=meta.id,
            client=client,
            sdk_session_id=None,
            status="running",
        )
        session_manager.sessions[meta.id] = managed

        await session_manager._consume_messages(managed)

        updated_meta = await meta_store.get(meta.id)
        assert updated_meta is not None
        assert managed.sdk_session_id == sdk_session_id
        assert updated_meta.sdk_session_id == sdk_session_id
        assert managed.status == "completed"
        assert updated_meta.status == "completed"
