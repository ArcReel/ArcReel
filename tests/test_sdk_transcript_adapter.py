"""Unit tests for SdkTranscriptAdapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter


class TestSdkTranscriptAdapterLegacyPath:
    """Tests for the filesystem fallback path (store=None)."""

    async def test_read_raw_messages_returns_adapted_messages(self):
        """SDK messages are adapted to the internal dict format."""
        mock_msg = MagicMock()
        mock_msg.type = "user"
        mock_msg.message = {"content": "Hello"}
        mock_msg.uuid = "uuid-123"
        mock_msg.parent_tool_use_id = None
        mock_msg.timestamp = "2026-03-05T00:00:00Z"

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[mock_msg],
        ):
            adapter = SdkTranscriptAdapter()
            result = await adapter.read_raw_messages("sdk-session-123")

        assert len(result) == 1
        assert result[0]["type"] == "user"
        assert result[0]["content"] == "Hello"
        assert result[0]["uuid"] == "uuid-123"
        assert result[0]["timestamp"] == "2026-03-05T00:00:00Z"

    async def test_read_raw_messages_empty_session_id(self):
        """Empty session ID returns empty list."""
        adapter = SdkTranscriptAdapter()
        assert await adapter.read_raw_messages("") == []
        assert await adapter.read_raw_messages(None) == []

    async def test_read_raw_messages_sdk_error_returns_empty(self):
        """SDK exceptions are caught and return empty list."""
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            side_effect=RuntimeError("SDK error"),
        ):
            adapter = SdkTranscriptAdapter()
            assert await adapter.read_raw_messages("sdk-session-123") == []

    async def test_parent_tool_use_id_preserved(self):
        """parent_tool_use_id is included when present."""
        mock_msg = MagicMock()
        mock_msg.type = "user"
        mock_msg.message = {"content": [{"type": "tool_result", "tool_use_id": "t1"}]}
        mock_msg.uuid = "uuid-456"
        mock_msg.parent_tool_use_id = "task-1"
        mock_msg.timestamp = None

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[mock_msg],
        ):
            adapter = SdkTranscriptAdapter()
            result = await adapter.read_raw_messages("sdk-session-123")

        assert result[0]["parent_tool_use_id"] == "task-1"

    async def test_exists_returns_true_when_messages_found(self):
        """exists() returns True when session has messages."""
        mock_msg = MagicMock()
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[mock_msg],
        ):
            adapter = SdkTranscriptAdapter()
            assert await adapter.exists("sdk-session-123") is True

    async def test_exists_returns_false_when_no_messages(self):
        """exists() returns False for empty or missing sessions."""
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[],
        ):
            adapter = SdkTranscriptAdapter()
            assert await adapter.exists("sdk-session-123") is False

    async def test_exists_returns_false_on_empty_id(self):
        adapter = SdkTranscriptAdapter()
        assert await adapter.exists("") is False
        assert await adapter.exists(None) is False

    async def test_exists_returns_false_on_sdk_error(self):
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            side_effect=RuntimeError("SDK error"),
        ):
            adapter = SdkTranscriptAdapter()
            assert await adapter.exists("sdk-session-123") is False

    async def test_assistant_message_content_is_list(self):
        """Assistant messages preserve content as-is (list of blocks)."""
        mock_msg = MagicMock()
        mock_msg.type = "assistant"
        mock_msg.message = {"content": [{"type": "text", "text": "Hello"}]}
        mock_msg.uuid = "uuid-789"
        mock_msg.parent_tool_use_id = None
        mock_msg.timestamp = "2026-03-05T00:00:01Z"

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[mock_msg],
        ):
            adapter = SdkTranscriptAdapter()
            result = await adapter.read_raw_messages("sdk-session-123")

        assert result[0]["type"] == "assistant"
        assert result[0]["content"] == [{"type": "text", "text": "Hello"}]


class TestSdkTranscriptAdapterStorePath:
    """Tests for the SessionStore-backed read path."""

    @pytest.mark.asyncio
    async def test_read_via_store_returns_adapted_messages(self):
        """Store path uses get_session_messages_from_store and inherits timestamp from SessionMessage.

        SessionMessage.timestamp is round-tripped from the payload.timestamp we
        persist in DbSessionStore (Task 4), so no JSONL backfill is required.
        """
        mock_msg = MagicMock()
        mock_msg.type = "user"
        mock_msg.message = {"content": "Hello"}
        mock_msg.uuid = "uuid-store"
        mock_msg.parent_tool_use_id = None
        mock_msg.timestamp = "2026-05-01T00:00:00Z"

        fake_store = object()
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages_from_store",
            new=AsyncMock(return_value=[mock_msg]),
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            result = await adapter.read_raw_messages("sdk-session-store", project_cwd="/tmp/proj")

        assert len(result) == 1
        assert result[0]["timestamp"] == "2026-05-01T00:00:00Z"
        assert result[0]["type"] == "user"
        assert result[0]["content"] == "Hello"
        assert result[0]["uuid"] == "uuid-store"

    @pytest.mark.asyncio
    async def test_read_via_store_passes_directory(self):
        """The store helper receives the project_cwd as `directory=`."""
        fake_store = object()
        helper = AsyncMock(return_value=[])
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages_from_store",
            new=helper,
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            await adapter.read_raw_messages("sdk-session-x", project_cwd="/tmp/proj")
        helper.assert_awaited_once()
        args, kwargs = helper.call_args
        assert args[0] is fake_store
        assert args[1] == "sdk-session-x"
        assert kwargs.get("directory") == "/tmp/proj"

    @pytest.mark.asyncio
    async def test_read_via_store_returns_empty_on_error(self):
        """Store helper exceptions are swallowed and returned as an empty list."""
        fake_store = object()
        helper = AsyncMock(side_effect=RuntimeError("boom"))
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages_from_store",
            new=helper,
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            result = await adapter.read_raw_messages("sdk-session-x", project_cwd="/tmp/proj")
        assert result == []

    @pytest.mark.asyncio
    async def test_exists_via_store_uses_limit_one(self):
        """exists() on the store path requests at most one message."""
        fake_store = object()
        mock_msg = MagicMock()
        helper = AsyncMock(return_value=[mock_msg])
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages_from_store",
            new=helper,
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            assert await adapter.exists("sdk-session-x", project_cwd="/tmp/proj") is True
        helper.assert_awaited_once()
        _, kwargs = helper.call_args
        assert kwargs.get("limit") == 1
        assert kwargs.get("directory") == "/tmp/proj"
