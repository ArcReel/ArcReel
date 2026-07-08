"""Unit tests for message serialization pure functions.

These feed SDK-message data directly to the module functions without
constructing a SessionManager instance.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from server.agent_runtime.message_serialization import (
    IMAGE_ONLY_SENTINEL,
    MESSAGE_TYPE_MAP,
    TASK_MESSAGE_SUBTYPES,
    build_runtime_status_message,
    infer_message_type,
    is_duplicate_user_echo,
    message_to_dict,
    serialize_value,
)


class TextBlock(BaseModel):
    """Mock SDK TextBlock."""

    type: str = "text"
    text: str


class ContentMessage(BaseModel):
    """Mock SDK message with nested content blocks."""

    type: str = "assistant"
    content: list[TextBlock]


@dataclass
class DataclassBlock:
    """Dataclass to test __dict__ serialization."""

    kind: str
    value: str


class TestSerializeValue:
    def test_serialize_primitives(self):
        assert serialize_value(None) is None
        assert serialize_value(True)
        assert serialize_value(42) == 42
        assert serialize_value(3.14) == 3.14
        assert serialize_value("hello") == "hello"

    def test_serialize_dict(self):
        data = {"key": "value", "nested": {"a": 1}}
        result = serialize_value(data)
        assert result == {"key": "value", "nested": {"a": 1}}

    def test_serialize_list(self):
        data = [1, "two", {"three": 3}]
        result = serialize_value(data)
        assert result == [1, "two", {"three": 3}]

    def test_serialize_pydantic_model(self):
        block = TextBlock(text="Hello world")
        result = serialize_value(block)
        assert result == {"type": "text", "text": "Hello world"}

    def test_serialize_nested_pydantic(self):
        """Test nested Pydantic models are fully serialized."""
        msg = ContentMessage(
            content=[
                TextBlock(text="First block"),
                TextBlock(text="Second block"),
            ]
        )
        result = serialize_value(msg)

        assert isinstance(result, dict)
        assert result["type"] == "assistant"
        assert isinstance(result["content"], list)
        assert len(result["content"]) == 2
        assert result["content"][0] == {"type": "text", "text": "First block"}
        assert result["content"][1] == {"type": "text", "text": "Second block"}

    def test_serialize_dataclass(self):
        block = DataclassBlock(kind="text", value="content")
        result = serialize_value(block)
        assert result == {"kind": "text", "value": "content"}

    def test_serialize_pydantic_with_json_mode_types(self):
        """Pydantic dump must go through mode='json' (datetime → ISO string)."""

        class Event(BaseModel):
            ts: datetime

        ts = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)
        result = serialize_value(Event(ts=ts))
        assert result == {"ts": "2026-04-19T12:00:00Z"}

    def test_serialize_unknown_object_to_string(self):
        """Objects without model_dump or __dict__ are converted to string."""

        class CustomObj:
            __slots__ = ()

            def __str__(self):
                return "custom-string"

            def __repr__(self):
                return "custom-string"

        result = serialize_value(CustomObj())
        assert result == "custom-string"


class UserMessage:
    """Mock SDK message whose class name drives type inference."""

    def __init__(self, content):
        self.content = content


class TaskStartedMessage:
    """Mock SDK typed-task message whose class name drives subtype injection."""

    def __init__(self, info):
        self.info = info


class TestInferMessageType:
    def test_message_type_map_includes_task_messages(self):
        """TaskMessage subclasses map to 'system' type."""
        assert MESSAGE_TYPE_MAP["TaskStartedMessage"] == "system"
        assert MESSAGE_TYPE_MAP["TaskProgressMessage"] == "system"
        assert MESSAGE_TYPE_MAP["TaskNotificationMessage"] == "system"

    def test_task_message_subtypes(self):
        """TaskMessage subtypes are correctly defined."""
        assert TASK_MESSAGE_SUBTYPES["TaskStartedMessage"] == "task_started"
        assert TASK_MESSAGE_SUBTYPES["TaskProgressMessage"] == "task_progress"
        assert TASK_MESSAGE_SUBTYPES["TaskNotificationMessage"] == "task_notification"

    def test_infer_returns_none_for_unknown_class(self):
        assert infer_message_type(object()) is None


class TestMessageToDict:
    def test_infers_type_from_class_name(self):
        result = message_to_dict(UserMessage("hi"))
        assert result == {"content": "hi", "type": "user"}

    def test_injects_task_subtype(self):
        result = message_to_dict(TaskStartedMessage("x"))
        assert result["type"] == "system"
        assert result["subtype"] == "task_started"

    def test_preserves_existing_type(self):
        result = message_to_dict({"type": "custom", "value": 1})
        assert result == {"type": "custom", "value": 1}


class TestBuildRuntimeStatusMessage:
    def test_error_status(self):
        status = build_runtime_status_message("error", "s1")
        assert status["type"] == "runtime_status"
        assert status["is_error"] is True
        assert status["status"] == "error"
        assert status["subtype"] == "error"
        assert status["session_id"] == "s1"
        assert status["uuid"].startswith("runtime-status-")

    def test_non_error_status(self):
        status = build_runtime_status_message("idle", "s2")
        assert status["is_error"] is False


class TestIsDuplicateUserEcho:
    def test_empty_queue_never_duplicate(self):
        assert is_duplicate_user_echo([], {"type": "user", "content": "hi"}) is False

    def test_matching_text_pops_and_returns_true(self):
        pending = ["hi"]
        assert is_duplicate_user_echo(pending, {"type": "user", "content": " hi "}) is True
        assert pending == []

    def test_non_matching_text_keeps_queue(self):
        pending = ["hi"]
        assert is_duplicate_user_echo(pending, {"type": "user", "content": "bye"}) is False
        assert pending == ["hi"]

    def test_image_only_sentinel_matches_empty_content(self):
        pending = [IMAGE_ONLY_SENTINEL]
        assert is_duplicate_user_echo(pending, {"type": "user", "content": []}) is True
        assert pending == []

    def test_empty_content_without_sentinel_not_duplicate(self):
        pending = ["hi"]
        assert is_duplicate_user_echo(pending, {"type": "user", "content": []}) is False
        assert pending == ["hi"]
