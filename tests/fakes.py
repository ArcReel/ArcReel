"""Shared fake / stub objects for tests.

Only objects used across multiple test files belong here.
Single-file fakes stay in their respective test modules.
"""

from __future__ import annotations


class FakeSDKClient:
    """Fake Claude Agent SDK client for testing SessionManager.

    Consolidates the three FakeClient variants previously scattered across
    ``test_session_manager_user_input`` and ``test_session_manager_sdk_session_id``.

    Usage::

        # Simple (no streaming):
        client = FakeSDKClient()

        # With pre-loaded streaming messages:
        client = FakeSDKClient(messages=[stream_event, result_msg])

        # Track queries / interrupts:
        await client.query("hello")
        assert client.sent_queries == ["hello"]
    """

    def __init__(self, messages=None):
        self._messages = list(messages) if messages else []
        self.sent_queries: list[str] = []
        self.interrupted = False
        self.disconnected = False

    async def query(self, content: str) -> None:
        self.sent_queries.append(content)

    async def interrupt(self) -> None:
        self.interrupted = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def connect(self) -> None:
        self.disconnected = False

    async def receive_response(self):
        for message in self._messages:
            yield message
