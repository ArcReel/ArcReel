"""Protocol contracts for the embedded agent's OpenAI bridge."""

import copy
import json

import pytest
from anthropic.lib.streaming._messages import accumulate_event
from anthropic.types import Message, RawMessageStreamEvent
from pydantic import TypeAdapter

from lib.agent.openai_messages import (
    ConversionError,
    ResponsesStreamTranslator,
    anthropic_usage,
    from_responses_response,
    to_responses_request,
)


@pytest.fixture
def request_body():
    return {
        "model": "gpt-example",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": "Create a scene."}],
        "tools": [
            {
                "name": "create_scene",
                "description": "Create a scene",
                "input_schema": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            }
        ],
    }


@pytest.fixture
def tool_response():
    return {
        "id": "resp_example",
        "model": "gpt-example",
        "status": "completed",
        "output": [
            {"type": "reasoning", "id": "rs_example", "summary": [], "encrypted_content": "opaque-ciphertext"},
            {
                "type": "message",
                "id": "msg_comment",
                "role": "assistant",
                "status": "completed",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "Preparing the scene.", "annotations": []}],
            },
            {
                "type": "function_call",
                "id": "fc_example",
                "call_id": "call_example",
                "name": "create_scene",
                "arguments": '{"title":"Evening"}',
                "status": "completed",
            },
        ],
        "usage": {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 40}, "output_tokens": 20},
    }


def test_request_preserves_system_text_tool_schema_and_effort(request_body):
    request_body.update(
        {
            "system": [
                {"type": "text", "text": "Be a director.", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "Use tools."},
            ],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
            "tool_choice": {"type": "any", "disable_parallel_tool_use": False},
        }
    )
    original = copy.deepcopy(request_body)
    payload, mapping = to_responses_request(request_body)
    assert payload["instructions"] == "Be a director.\n\nUse tools."
    assert payload["tools"][0]["parameters"] == request_body["tools"][0]["input_schema"]
    assert payload["tools"][0]["strict"] is False
    assert payload["store"] is False
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["tool_choice"] == "required"
    assert payload["parallel_tool_calls"] is True
    assert payload["max_output_tokens"] == 1000
    assert mapping == {"create_scene": "create_scene"}
    assert request_body == original


@pytest.mark.parametrize("display", ["updates", "summarized", "omitted"])
def test_native_cli_keep_all_context_and_display_hint_preserve_reasoning(request_body, display):
    request_body["thinking"] = {"type": "adaptive", "display": display}
    request_body["context_management"] = {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]}
    payload, _ = to_responses_request(request_body)
    assert payload["reasoning"] == {"effort": "medium"}
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert "context_management" not in payload
    assert "thinking" not in payload


@pytest.mark.parametrize("display", ["unknown", "", 1, {"mode": "omitted"}])
def test_unknown_thinking_display_is_rejected_without_echoing_input(request_body, display):
    request_body["thinking"] = {"type": "adaptive", "display": display}
    with pytest.raises(ConversionError, match=r"^Unsupported thinking display mode$"):
        to_responses_request(request_body)


@pytest.mark.parametrize("role", ["system", "developer"])
def test_native_cli_per_turn_instructions_keep_their_priority(request_body, role):
    request_body["messages"].append({"role": role, "content": [{"type": "text", "text": "Use the current project."}]})
    payload, _ = to_responses_request(request_body)
    assert [message["role"] for message in payload["input"]] == ["user", role]
    assert payload["input"][1]["content"] == [{"type": "input_text", "text": "Use the current project."}]


@pytest.mark.parametrize("role", ["system", "developer"])
def test_high_priority_messages_reject_nontext_content(request_body, role):
    request_body["messages"].append(
        {
            "role": role,
            "content": [{"type": "image", "source": {"type": "url", "url": "https://example.test/image.png"}}],
        }
    )
    with pytest.raises(ConversionError, match="must contain only text"):
        to_responses_request(request_body)


@pytest.mark.parametrize(
    ("model", "thinking", "effort", "expected"),
    [
        ("gpt-6.1-sol", "disabled", None, "low"),
        ("gpt-6.1-sol", "adaptive", "none", "low"),
        ("gpt-6.1-sol", "adaptive", "minimal", "low"),
        ("gpt-6.1-sol", "adaptive", "high", "high"),
        ("gpt-6.1-sol", "adaptive", "max", "max"),
        ("gpt-6.1-sol-2026-10-01", "disabled", None, "low"),
        ("gpt-example", "disabled", None, "none"),
        ("gpt-example", "adaptive", "minimal", "minimal"),
    ],
)
def test_reasoning_effort_respects_sol_minimum_without_changing_other_models(
    request_body, model, thinking, effort, expected
):
    request_body["model"] = model
    request_body["thinking"] = {"type": thinking}
    if effort is not None:
        request_body["output_config"] = {"effort": effort}
    payload, _ = to_responses_request(request_body)
    assert payload["reasoning"] == {"effort": expected}


def test_request_preserves_images_inside_and_outside_tool_results(request_body):
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aW1hZ2U="}}
    request_body["messages"] = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call_one", "name": "create_scene", "input": {"title": "First"}},
                {"type": "tool_use", "id": "call_two", "name": "create_scene", "input": {"title": "Second"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_one",
                    "content": [image, {"type": "text", "text": "Rendered"}],
                },
                {"type": "tool_result", "tool_use_id": "call_two", "is_error": True, "content": "Render failed"},
                image,
                {"type": "text", "text": "Use this reference."},
            ],
        },
    ]
    payload, _ = to_responses_request(request_body)
    first, second, first_result, second_result, user = payload["input"]
    assert [first["call_id"], second["call_id"]] == ["call_one", "call_two"]
    assert first_result["call_id"] == "call_one"
    assert first_result["output"] == [
        {"type": "input_image", "image_url": "data:image/png;base64,aW1hZ2U=", "detail": "auto"},
        {"type": "input_text", "text": "Rendered"},
    ]
    assert second_result["output"] == [
        {"type": "input_text", "text": "Tool execution failed."},
        {"type": "input_text", "text": "Render failed"},
    ]
    assert user["content"][0] == first_result["output"][0]
    assert user["content"][1]["text"] == "Use this reference."


def test_long_names_are_stable_distinct_and_restored(request_body, tool_response):
    names = ["mcp__project__" + "shared_prefix_" * 6 + suffix for suffix in ("one", "two")]
    request_body["tools"] = [{**request_body["tools"][0], "name": name} for name in names]
    request_body["tool_choice"] = {"type": "tool", "name": names[1]}
    payload, mapping = to_responses_request(request_body)
    aliases = [tool["name"] for tool in payload["tools"]]
    assert len(set(aliases)) == 2
    assert all(len(alias) <= 64 for alias in aliases)
    assert payload["tool_choice"] == {"type": "function", "name": aliases[1]}
    request_body["tools"].reverse()
    assert to_responses_request(request_body)[1] == mapping
    tool_response["output"][-1]["name"] = aliases[0]
    converted = from_responses_response(tool_response, mapping)
    assert next(block for block in converted["content"] if block["type"] == "tool_use")["name"] == names[0]


def test_resume_retains_encrypted_reasoning_phase_and_tool_identity(request_body, tool_response):
    converted = from_responses_response(tool_response, {"create_scene": "create_scene"})
    TypeAdapter(Message).validate_python(converted)
    assert converted["stop_reason"] == "tool_use"
    assert converted["usage"] == {
        "input_tokens": 60,
        "output_tokens": 20,
        "cache_read_input_tokens": 40,
        "cache_creation_input_tokens": 0,
    }
    request_body["messages"].append({"role": "assistant", "content": converted["content"]})
    request_body["messages"].append(
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_example", "content": "Created"}]}
    )
    resumed, _ = to_responses_request(request_body)
    assert resumed["input"][1:3] == tool_response["output"][:2]
    assert json.loads(resumed["input"][3]["arguments"]) == {"title": "Evening"}
    assert {key: value for key, value in resumed["input"][3].items() if key != "arguments"} == {
        key: value for key, value in tool_response["output"][2].items() if key != "arguments"
    }
    assert resumed["input"][-1]["call_id"] == "call_example"


def test_resume_visible_text_remains_authoritative(request_body, tool_response):
    converted = from_responses_response(tool_response, {"create_scene": "create_scene"})
    for block in converted["content"]:
        if block["type"] == "text":
            block["text"] = "Corrected commentary."
    request_body["messages"].append({"role": "assistant", "content": converted["content"]})
    resumed, _ = to_responses_request(request_body)
    assert resumed["input"][2]["content"][0]["text"] == "Corrected commentary."
    assert resumed["input"][2]["phase"] == "commentary"


def test_reordered_parallel_tool_metadata_restores_by_call_id(request_body, tool_response):
    first = tool_response["output"][-1]
    second = {**first, "id": "fc_second", "call_id": "call_second", "arguments": '{"title":"Morning"}'}
    tool_response["output"] = [first, second]
    converted = from_responses_response(tool_response, {"create_scene": "create_scene"})
    first_tool, first_metadata, second_tool, second_metadata = converted["content"]
    request_body["messages"].append(
        {"role": "assistant", "content": [first_metadata, first_tool, second_tool, second_metadata]}
    )
    request_body["messages"].append(
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": item["call_id"], "content": "Created"}
                for item in (first, second)
            ],
        }
    )
    resumed, _ = to_responses_request(request_body)
    restored = resumed["input"][1:-2]
    assert [(item["id"], item["call_id"]) for item in restored] == [
        ("fc_example", "call_example"),
        ("fc_second", "call_second"),
    ]


def test_tool_metadata_cannot_attach_to_an_earlier_turn(request_body, tool_response):
    converted = from_responses_response(tool_response, {"create_scene": "create_scene"})
    request_body["messages"].extend(
        [
            {"role": "assistant", "content": converted["content"][:-1]},
            {"role": "user", "content": "Start a different turn."},
            {"role": "assistant", "content": converted["content"][-1:]},
        ]
    )
    with pytest.raises(ConversionError, match="no matching assistant item"):
        to_responses_request(request_body)


@pytest.mark.parametrize(
    "content",
    [
        [{"type": "document", "source": {"type": "text", "data": "important text"}}],
        [{"type": "input_audio", "data": "audio"}],
        [
            {
                "type": "tool_result",
                "tool_use_id": "call_one",
                "content": [{"type": "document", "source": {"type": "text", "data": "document"}}],
            }
        ],
    ],
)
def test_unsupported_user_content_is_rejected_not_dropped(request_body, content):
    request_body["messages"] = [{"role": "user", "content": content}]
    with pytest.raises(ConversionError, match="Unsupported"):
        to_responses_request(request_body)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stop_sequences", ["STOP"]),
        ("context_management", {"edits": [{"type": "clear_tool_uses_20250919"}]}),
        (
            "context_management",
            {"edits": [{"type": "clear_thinking_20251015", "keep": {"type": "thinking_turns", "value": 1}}]},
        ),
        ("safeguards", {"dangerous_tool_use": {"context": "classifier context"}}),
        ("output_config", {"format": {"type": "json_schema", "schema": {"type": "object"}}}),
    ],
)
def test_unsupported_request_features_fail_explicitly(request_body, field, value):
    request_body[field] = value
    with pytest.raises(ConversionError, match="Unsupported"):
        to_responses_request(request_body)


@pytest.mark.parametrize(
    "block",
    [
        {"type": "thinking", "thinking": "provider-private", "signature": "signature"},
        {"type": "redacted_thinking", "data": "other-provider-ciphertext"},
        {"type": "redacted_thinking", "data": "arcreel-openai-v1:not-base64"},
    ],
)
def test_incompatible_reasoning_history_fails_without_echoing_data(request_body, block):
    request_body["messages"] = [{"role": "assistant", "content": [block]}]
    with pytest.raises(ConversionError) as exc:
        to_responses_request(request_body)
    assert "provider-private" not in str(exc.value)
    assert "other-provider-ciphertext" not in str(exc.value)


def test_stream_matches_full_conversion_using_anthropic_accumulator(tool_response):
    translator = ResponsesStreamTranslator({"create_scene": "create_scene"}, "gpt-example")
    events = translator.feed({"type": "response.created", "response": {"id": "resp_example", "usage": None}})
    events.extend(
        translator.feed({"type": "response.output_item.done", "output_index": 0, "item": tool_response["output"][0]})
    )
    events.extend(
        translator.feed(
            {"type": "response.output_text.delta", "output_index": 1, "content_index": 0, "delta": "Preparing "}
        )
    )
    events.extend(
        translator.feed(
            {"type": "response.output_text.delta", "output_index": 1, "content_index": 0, "delta": "the scene."}
        )
    )
    events.extend(
        translator.feed({"type": "response.output_item.done", "output_index": 1, "item": tool_response["output"][1]})
    )
    events.extend(
        translator.feed(
            {
                "type": "response.output_item.added",
                "output_index": 2,
                "item": {**tool_response["output"][2], "arguments": ""},
            }
        )
    )
    events.extend(
        translator.feed({"type": "response.function_call_arguments.delta", "output_index": 2, "delta": '{"title":'})
    )
    events.extend(
        translator.feed({"type": "response.function_call_arguments.delta", "output_index": 2, "delta": '"Evening"}'})
    )
    events.extend(
        translator.feed({"type": "response.output_item.done", "output_index": 2, "item": tool_response["output"][2]})
    )
    events.extend(translator.feed({"type": "response.completed", "response": tool_response}))
    assert translator.finish() == []
    snapshot = None
    for event in events:
        parsed = TypeAdapter(RawMessageStreamEvent).validate_python(event)
        snapshot = accumulate_event(event=parsed, current_snapshot=snapshot)
    assert snapshot is not None
    actual = snapshot.model_dump(exclude_none=True)
    expected = from_responses_response(tool_response, {"create_scene": "create_scene"})
    assert actual["content"] == expected["content"]
    assert actual["usage"] == expected["usage"]
    assert actual["stop_reason"] == "tool_use"
    assert sum(event["type"] == "message_stop" for event in events) == 1


def test_interleaved_parallel_tool_stream_preserves_each_item_id(request_body, tool_response):
    first = tool_response["output"][-1]
    second = {**first, "id": "fc_second", "call_id": "call_second", "arguments": '{"title":"Morning"}'}
    response = {**tool_response, "output": [first, second]}
    translator = ResponsesStreamTranslator({"create_scene": "create_scene"}, "gpt-example")
    events = translator.feed({"type": "response.created", "response": response})
    for index, item in enumerate([first, second]):
        events.extend(
            translator.feed(
                {"type": "response.output_item.added", "output_index": index, "item": {**item, "arguments": ""}}
            )
        )
    for index, item in enumerate([first, second]):
        events.extend(
            translator.feed(
                {"type": "response.function_call_arguments.delta", "output_index": index, "delta": item["arguments"]}
            )
        )
        events.extend(translator.feed({"type": "response.output_item.done", "output_index": index, "item": item}))
    events.extend(translator.feed({"type": "response.completed", "response": response}))
    snapshot = None
    for event in events:
        snapshot = accumulate_event(
            event=TypeAdapter(RawMessageStreamEvent).validate_python(event), current_snapshot=snapshot
        )
    request_body["messages"].append({"role": "assistant", "content": snapshot.model_dump(exclude_none=True)["content"]})
    resumed, _ = to_responses_request(request_body)
    assert [(item["id"], item["call_id"]) for item in resumed["input"][1:]] == [
        ("fc_example", "call_example"),
        ("fc_second", "call_second"),
    ]


@pytest.mark.parametrize(
    ("status", "details", "content", "stop"),
    [
        ("completed", None, [{"type": "output_text", "text": "Done", "annotations": []}], "end_turn"),
        (
            "incomplete",
            {"reason": "max_output_tokens"},
            [{"type": "output_text", "text": "Partial", "annotations": []}],
            "max_tokens",
        ),
        ("incomplete", {"reason": "content_filter"}, [{"type": "refusal", "refusal": "Cannot help."}], "refusal"),
    ],
)
def test_terminal_stop_reasons_and_final_phase(tool_response, status, details, content, stop):
    response = {
        **tool_response,
        "status": status,
        "incomplete_details": details,
        "output": [
            {
                "id": "msg_final",
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "status": "incomplete" if status == "incomplete" else "completed",
                "content": content,
            }
        ],
    }
    converted = from_responses_response(response, {})
    assert converted["stop_reason"] == stop
    translator = ResponsesStreamTranslator({}, "gpt-example")
    events = translator.feed(
        {"type": "response.incomplete" if status == "incomplete" else "response.completed", "response": response}
    )
    assert events[-2]["delta"]["stop_reason"] == stop
    assert events[-1]["type"] == "message_stop"


def test_upstream_failure_is_not_a_success_or_raw_error_disclosure(tool_response):
    failed = {
        **tool_response,
        "status": "failed",
        "error": {"message": "sensitive upstream detail", "code": "server_error"},
    }
    assert from_responses_response(failed, {}) == {
        "type": "error",
        "error": {"type": "api_error", "message": "OpenAI did not complete the response."},
    }
    translator = ResponsesStreamTranslator({}, "gpt-example")
    events = translator.feed({"type": "response.failed", "response": failed})
    assert events[0]["type"] == "error"
    assert "sensitive upstream detail" not in json.dumps(events)
    assert translator.finish() == []


def test_truncated_stream_fails_instead_of_synthesizing_success():
    translator = ResponsesStreamTranslator({}, "gpt-example")
    translator.feed({"type": "response.created", "response": {"id": "resp_short"}})
    translator.feed(
        {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "unfinished"}
    )
    with pytest.raises(ConversionError, match="before completion"):
        translator.finish()


def test_invalid_function_json_is_rejected(tool_response):
    tool_response["output"] = [{**tool_response["output"][-1], "arguments": "[]"}]
    with pytest.raises(ConversionError, match="function arguments must be an object"):
        from_responses_response(tool_response, {"create_scene": "create_scene"})


def test_usage_separates_cache_tokens_without_counting_them_twice():
    assert anthropic_usage(
        {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 30, "cache_write_tokens": 10},
            "output_tokens": 8,
        }
    ) == {
        "input_tokens": 60,
        "cache_read_input_tokens": 30,
        "cache_creation_input_tokens": 10,
        "output_tokens": 8,
    }
