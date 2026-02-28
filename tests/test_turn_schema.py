"""Unit tests for turn_schema shared normalization."""

from server.agent_runtime.turn_schema import (
    infer_block_type,
    normalize_block,
    normalize_content,
    normalize_turn,
    normalize_turns,
)


class TestInferBlockType:
    def test_explicit_type_returned(self):
        assert infer_block_type({"type": "text", "text": "hi"}) == "text"

    def test_explicit_type_tool_use(self):
        assert infer_block_type({"type": "tool_use", "id": "t1", "name": "Read"}) == "tool_use"

    def test_tool_result_inferred_from_content(self):
        assert infer_block_type({"tool_use_id": "t1", "content": "ok"}) == "tool_result"

    def test_tool_result_inferred_from_is_error(self):
        assert infer_block_type({"tool_use_id": "t1", "is_error": False}) == "tool_result"

    def test_tool_use_inferred(self):
        assert infer_block_type({"id": "t1", "name": "Read", "input": {}}) == "tool_use"

    def test_thinking_inferred(self):
        assert infer_block_type({"thinking": "hmm"}) == "thinking"

    def test_text_inferred(self):
        assert infer_block_type({"text": "hello"}) == "text"

    def test_empty_block(self):
        assert infer_block_type({}) == ""

    def test_explicit_type_takes_precedence(self):
        # Even if shape looks like tool_result, explicit type wins
        assert infer_block_type({"type": "text", "tool_use_id": "t1", "content": "x"}) == "text"


class TestNormalizeBlock:
    def test_string_becomes_text_block(self):
        result = normalize_block("hello")
        assert result == {"type": "text", "text": "hello"}

    def test_dict_without_type_gets_inferred_type(self):
        result = normalize_block({"text": "hi"})
        assert result["type"] == "text"
        assert result["text"] == "hi"

    def test_tool_use_input_coerced_to_dict(self):
        result = normalize_block({"type": "tool_use", "id": "t1", "name": "Read", "input": "bad"})
        assert result["input"] == {}

    def test_tool_use_input_preserved_when_dict(self):
        result = normalize_block({"type": "tool_use", "id": "t1", "name": "Read", "input": {"a": 1}})
        assert result["input"] == {"a": 1}

    def test_tool_use_input_none_coerced(self):
        result = normalize_block({"type": "tool_use", "id": "t1", "name": "Read", "input": None})
        assert result["input"] == {}

    def test_text_default_value(self):
        result = normalize_block({"type": "text"})
        assert result["text"] == ""

    def test_thinking_default_value(self):
        result = normalize_block({"type": "thinking"})
        assert result["thinking"] == ""

    def test_existing_text_preserved(self):
        result = normalize_block({"type": "text", "text": "hello"})
        assert result["text"] == "hello"

    def test_deep_copy_isolation(self):
        original = {"type": "text", "text": "hi"}
        result = normalize_block(original)
        result["text"] = "changed"
        assert original["text"] == "hi"

    def test_non_dict_non_string(self):
        result = normalize_block(42)
        assert result == {"type": "text", "text": "42"}

    def test_unknown_type_defaults_to_text(self):
        result = normalize_block({})
        assert result["type"] == "text"


class TestNormalizeContent:
    def test_string_to_list(self):
        result = normalize_content("hello")
        assert result == [{"type": "text", "text": "hello"}]

    def test_empty_string(self):
        assert normalize_content("") == []

    def test_whitespace_string(self):
        assert normalize_content("   ") == []

    def test_list_passthrough_with_normalization(self):
        result = normalize_content([{"type": "text", "text": "hi"}])
        assert len(result) == 1
        assert result[0]["type"] == "text"

    def test_list_with_untyped_blocks(self):
        result = normalize_content([{"text": "hi"}, {"id": "t1", "name": "Read", "input": {}}])
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "tool_use"

    def test_none_returns_empty(self):
        assert normalize_content(None) == []

    def test_number_returns_empty(self):
        assert normalize_content(42) == []


class TestNormalizeTurn:
    def test_string_content_normalized(self):
        turn = {"type": "user", "content": "hello"}
        result = normalize_turn(turn)
        assert isinstance(result["content"], list)
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "hello"

    def test_uuid_and_timestamp_defaults(self):
        turn = {"type": "assistant", "content": []}
        result = normalize_turn(turn)
        assert "uuid" in result
        assert result["uuid"] is None
        assert "timestamp" in result
        assert result["timestamp"] is None

    def test_existing_uuid_preserved(self):
        turn = {"type": "user", "content": [], "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z"}
        result = normalize_turn(turn)
        assert result["uuid"] == "u1"
        assert result["timestamp"] == "2026-01-01T00:00:00Z"

    def test_result_turn_gets_empty_content(self):
        turn = {"type": "result", "subtype": "success"}
        result = normalize_turn(turn)
        assert result["type"] == "result"
        assert result["content"] == []
        assert result["subtype"] == "success"

    def test_extra_fields_preserved(self):
        turn = {"type": "assistant", "content": [], "custom_field": "value"}
        result = normalize_turn(turn)
        assert result["custom_field"] == "value"

    def test_deep_copy_isolation(self):
        original_block = {"type": "text", "text": "hi"}
        turn = {"type": "assistant", "content": [original_block]}
        result = normalize_turn(turn)
        result["content"][0]["text"] = "changed"
        assert original_block["text"] == "hi"

    def test_draft_turn_shape(self):
        """Draft turns from stream_projector have uuid but no timestamp."""
        turn = {"type": "assistant", "content": [{"type": "text", "text": "thinking..."}], "uuid": "draft-abc"}
        result = normalize_turn(turn)
        assert result["uuid"] == "draft-abc"
        assert result["timestamp"] is None

    def test_tool_use_blocks_normalized(self):
        turn = {
            "type": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": "invalid"},
            ],
        }
        result = normalize_turn(turn)
        assert result["content"][0]["input"] == {}


class TestNormalizeTurns:
    def test_batch(self):
        turns = [
            {"type": "user", "content": "hello"},
            {"type": "assistant", "content": [{"text": "world"}]},
        ]
        result = normalize_turns(turns)
        assert len(result) == 2
        assert isinstance(result[0]["content"], list)
        assert result[1]["content"][0]["type"] == "text"
        assert result[0]["uuid"] is None
        assert result[1]["uuid"] is None

    def test_empty_list(self):
        assert normalize_turns([]) == []
