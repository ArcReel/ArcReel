"""Unit tests for SdkTranscriptAdapter."""

from unittest.mock import patch, MagicMock

from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter


class TestSdkTranscriptAdapter:
    def test_read_raw_messages_returns_adapted_messages(self):
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
            result = adapter.read_raw_messages("sdk-session-123")

        assert len(result) == 1
        assert result[0]["type"] == "user"
        assert result[0]["content"] == "Hello"
        assert result[0]["uuid"] == "uuid-123"

    def test_read_raw_messages_empty_session_id(self):
        """Empty session ID returns empty list."""
        adapter = SdkTranscriptAdapter()
        assert adapter.read_raw_messages("") == []
        assert adapter.read_raw_messages(None) == []

    def test_read_raw_messages_sdk_error_returns_empty(self):
        """SDK exceptions are caught and return empty list."""
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            side_effect=RuntimeError("SDK error"),
        ):
            adapter = SdkTranscriptAdapter()
            assert adapter.read_raw_messages("sdk-session-123") == []

    def test_parent_tool_use_id_preserved(self):
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
            result = adapter.read_raw_messages("sdk-session-123")

        assert result[0]["parent_tool_use_id"] == "task-1"

    def test_exists_returns_true_when_messages_found(self):
        """exists() returns True when session has messages."""
        mock_msg = MagicMock()
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[mock_msg],
        ):
            adapter = SdkTranscriptAdapter()
            assert adapter.exists("sdk-session-123") is True

    def test_exists_returns_false_when_no_messages(self):
        """exists() returns False for empty or missing sessions."""
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[],
        ):
            adapter = SdkTranscriptAdapter()
            assert adapter.exists("sdk-session-123") is False

    def test_exists_returns_false_on_empty_id(self):
        adapter = SdkTranscriptAdapter()
        assert adapter.exists("") is False
        assert adapter.exists(None) is False

    def test_exists_returns_false_on_sdk_error(self):
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            side_effect=RuntimeError("SDK error"),
        ):
            adapter = SdkTranscriptAdapter()
            assert adapter.exists("sdk-session-123") is False

    def test_assistant_message_content_is_list(self):
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
            result = adapter.read_raw_messages("sdk-session-123")

        assert result[0]["type"] == "assistant"
        assert result[0]["content"] == [{"type": "text", "text": "Hello"}]
