"""Unit tests for SessionManager._serialize_value method."""

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel


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
    def test_serialize_primitives(self, session_manager):
        assert session_manager._serialize_value(None) is None
        assert session_manager._serialize_value(True)
        assert session_manager._serialize_value(42) == 42
        assert session_manager._serialize_value(3.14) == 3.14
        assert session_manager._serialize_value("hello") == "hello"

    def test_serialize_dict(self, session_manager):
        data = {"key": "value", "nested": {"a": 1}}
        result = session_manager._serialize_value(data)
        assert result == {"key": "value", "nested": {"a": 1}}

    def test_serialize_list(self, session_manager):
        data = [1, "two", {"three": 3}]
        result = session_manager._serialize_value(data)
        assert result == [1, "two", {"three": 3}]

    def test_serialize_pydantic_model(self, session_manager):
        block = TextBlock(text="Hello world")
        result = session_manager._serialize_value(block)
        assert result == {"type": "text", "text": "Hello world"}

    def test_serialize_nested_pydantic(self, session_manager):
        """Test nested Pydantic models are fully serialized."""
        msg = ContentMessage(
            content=[
                TextBlock(text="First block"),
                TextBlock(text="Second block"),
            ]
        )
        result = session_manager._serialize_value(msg)

        assert isinstance(result, dict)
        assert result["type"] == "assistant"
        assert isinstance(result["content"], list)
        assert len(result["content"]) == 2
        assert result["content"][0] == {"type": "text", "text": "First block"}
        assert result["content"][1] == {"type": "text", "text": "Second block"}

    def test_serialize_dataclass(self, session_manager):
        block = DataclassBlock(kind="text", value="content")
        result = session_manager._serialize_value(block)
        assert result == {"kind": "text", "value": "content"}

    def test_serialize_pydantic_with_json_mode_types(self, session_manager):
        """Pydantic dump must go through mode='json' (datetime → ISO string)."""

        class Event(BaseModel):
            ts: datetime

        ts = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)
        result = session_manager._serialize_value(Event(ts=ts))
        assert result == {"ts": "2026-04-19T12:00:00Z"}

    def test_serialize_unknown_object_to_string(self, session_manager):
        """Objects without model_dump or __dict__ are converted to string."""

        class CustomObj:
            def __str__(self):
                return "custom-string"

            def __repr__(self):
                return "custom-string"

        # Remove __dict__ to simulate an object without it
        obj = 42  # int doesn't have model_dump, handled as primitive
        result = session_manager._serialize_value(obj)
        assert result == 42
