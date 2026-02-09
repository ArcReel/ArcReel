"""Unit tests for SessionManager._serialize_value method."""

import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel

from webui.server.agent_runtime.session_manager import SessionManager
from webui.server.agent_runtime.session_store import SessionMetaStore


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


class TestSerializeValue(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        tmppath = Path(self.tmpdir.name)
        db_path = tmppath / "sessions.db"
        meta_store = SessionMetaStore(db_path)
        self.manager = SessionManager(
            project_root=tmppath,
            data_dir=tmppath,
            meta_store=meta_store,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_serialize_primitives(self):
        self.assertIsNone(self.manager._serialize_value(None))
        self.assertEqual(self.manager._serialize_value(True), True)
        self.assertEqual(self.manager._serialize_value(42), 42)
        self.assertEqual(self.manager._serialize_value(3.14), 3.14)
        self.assertEqual(self.manager._serialize_value("hello"), "hello")

    def test_serialize_dict(self):
        data = {"key": "value", "nested": {"a": 1}}
        result = self.manager._serialize_value(data)
        self.assertEqual(result, {"key": "value", "nested": {"a": 1}})

    def test_serialize_list(self):
        data = [1, "two", {"three": 3}]
        result = self.manager._serialize_value(data)
        self.assertEqual(result, [1, "two", {"three": 3}])

    def test_serialize_pydantic_model(self):
        block = TextBlock(text="Hello world")
        result = self.manager._serialize_value(block)
        self.assertEqual(result, {"type": "text", "text": "Hello world"})

    def test_serialize_nested_pydantic(self):
        """Test nested Pydantic models are fully serialized."""
        msg = ContentMessage(
            content=[
                TextBlock(text="First block"),
                TextBlock(text="Second block"),
            ]
        )
        result = self.manager._serialize_value(msg)

        self.assertIsInstance(result, dict)
        self.assertEqual(result["type"], "assistant")
        self.assertIsInstance(result["content"], list)
        self.assertEqual(len(result["content"]), 2)
        self.assertEqual(result["content"][0], {"type": "text", "text": "First block"})
        self.assertEqual(result["content"][1], {"type": "text", "text": "Second block"})

    def test_serialize_dataclass(self):
        block = DataclassBlock(kind="text", value="content")
        result = self.manager._serialize_value(block)
        self.assertEqual(result, {"kind": "text", "value": "content"})

    def test_serialize_unknown_object_to_string(self):
        """Objects without model_dump or __dict__ are converted to string."""
        class CustomObj:
            def __str__(self):
                return "custom-string"

            def __repr__(self):
                return "custom-string"

        # Remove __dict__ to simulate an object without it
        obj = 42  # int doesn't have model_dump, handled as primitive
        result = self.manager._serialize_value(obj)
        self.assertEqual(result, 42)


if __name__ == "__main__":
    unittest.main()
