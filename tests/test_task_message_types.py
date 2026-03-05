"""Tests for TaskMessage type handling in SessionManager."""

from server.agent_runtime.session_manager import SessionManager


class TestTaskMessageTypes:
    def test_message_type_map_includes_task_messages(self):
        """TaskMessage subclasses map to 'system' type."""
        assert SessionManager._MESSAGE_TYPE_MAP["TaskStartedMessage"] == "system"
        assert SessionManager._MESSAGE_TYPE_MAP["TaskProgressMessage"] == "system"
        assert SessionManager._MESSAGE_TYPE_MAP["TaskNotificationMessage"] == "system"

    def test_task_message_subtypes(self):
        """TaskMessage subtypes are correctly defined."""
        assert SessionManager._TASK_MESSAGE_SUBTYPES["TaskStartedMessage"] == "task_started"
        assert SessionManager._TASK_MESSAGE_SUBTYPES["TaskProgressMessage"] == "task_progress"
        assert SessionManager._TASK_MESSAGE_SUBTYPES["TaskNotificationMessage"] == "task_notification"
