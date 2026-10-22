"""Pure Messages/Responses conversion for the embedded agent's HTTP bridge.

The bridge preserves Responses item identity and phase in opaque Messages
``redacted_thinking`` blocks. Reasoning payloads contain encrypted content only;
the bridge never exposes or reconstructs private reasoning text. Cache hints
remain provider-owned. Anthropic thinking budgets become effort hints, since
Responses has no separate thinking-token budget.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

_STATE_PREFIX = "arcreel-openai-v1:"
_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_REQUEST_KEYS = {
    "model",
    "messages",
    "system",
    "max_tokens",
    "stream",
    "tools",
    "tool_choice",
    "thinking",
    "output_config",
    "temperature",
    "top_p",
    "metadata",
    "cache_control",
    "context_management",
}


class ConversionError(ValueError):
    """Unsupported or malformed bridge input; messages never include payloads."""


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConversionError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ConversionError(f"{label} must be a string")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConversionError(f"{label} must be an array")
    return value


def _reject_extra(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    if any(key not in allowed and item not in (None, [], {}) for key, item in value.items()):
        raise ConversionError(f"Unsupported {label} fields")


def _blocks(content: Any) -> list[Mapping[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [_object(block, "content block") for block in _list(content, "content")]


def _names(body: Mapping[str, Any]) -> dict[str, str]:
    names = {_string(_object(tool, "tool").get("name"), "tool name") for tool in _list(body.get("tools", []), "tools")}
    for message in _list(body.get("messages"), "messages"):
        for block in _blocks(_object(message, "message").get("content")):
            if block.get("type") == "tool_use":
                names.add(_string(block.get("name"), "tool name"))
    mapping = {name: name for name in names if _NAME_PATTERN.fullmatch(name)}
    for name in sorted(names - mapping.keys()):
        suffix = 0
        while True:
            digest = hashlib.sha256(f"{name}:{suffix}".encode()).hexdigest()[:32]
            prefix = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:31]
            alias = f"{prefix}_{digest}"
            if alias not in mapping:
                mapping[alias] = name
                break
            suffix += 1
    return mapping


def _image(block: Mapping[str, Any]) -> dict[str, Any]:
    source = _object(block.get("source"), "image source")
    if source.get("type") == "base64":
        media_type = source.get("media_type")
        if media_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            raise ConversionError("Unsupported image media type")
        url = f"data:{media_type};base64,{_string(source.get('data'), 'image data')}"
    elif source.get("type") == "url":
        url = _string(source.get("url"), "image URL")
    else:
        raise ConversionError("Unsupported image source")
    return {"type": "input_image", "image_url": url, "detail": "auto"}


def _input_part(block: Mapping[str, Any]) -> dict[str, Any]:
    if block.get("type") == "text":
        return {"type": "input_text", "text": _string(block.get("text"), "text")}
    if block.get("type") == "image":
        return _image(block)
    raise ConversionError("Unsupported message or tool-result content type")


def _state_block(kind: str, value: Mapping[str, Any]) -> dict[str, Any]:
    encoded = base64.b64encode(json.dumps({"kind": kind, "value": value}, separators=(",", ":")).encode()).decode()
    return {"type": "redacted_thinking", "data": _STATE_PREFIX + encoded}


def _read_state(block: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    data = _string(block.get("data"), "redacted thinking data")
    if not data.startswith(_STATE_PREFIX):
        raise ConversionError("This session contains reasoning from another provider; start a new OpenAI session")
    try:
        decoded = _object(json.loads(base64.b64decode(data[len(_STATE_PREFIX) :], validate=True)), "resume state")
    except (ValueError, binascii.Error, UnicodeError) as exc:
        raise ConversionError("Invalid OpenAI resume state") from exc
    return _string(decoded.get("kind"), "resume kind"), _object(decoded.get("value"), "resume value")


def _reasoning_item(item: Mapping[str, Any]) -> dict[str, Any]:
    encrypted = item.get("encrypted_content")
    if not isinstance(encrypted, str) or not encrypted:
        raise ConversionError("OpenAI reasoning is missing encrypted resume content")
    summary = []
    for raw in _list(item.get("summary", []), "reasoning summary"):
        part = _object(raw, "reasoning summary part")
        if part.get("type") != "summary_text":
            raise ConversionError("Unsupported reasoning summary")
        summary.append({"type": "summary_text", "text": _string(part.get("text"), "reasoning summary text")})
    return {
        "type": "reasoning",
        "id": _string(item.get("id"), "reasoning id"),
        "encrypted_content": encrypted,
        "summary": summary,
    }


def _restore_state(items: list[dict[str, Any]], kind: str, value: Mapping[str, Any]) -> None:
    if kind == "reasoning":
        items.append(_reasoning_item(value))
        return
    target = next(
        (
            item
            for item in reversed(items)
            if item.get("type") == kind and (kind != "function_call" or item.get("call_id") == value.get("call_id"))
        ),
        None,
    )
    if target is None:
        raise ConversionError("OpenAI resume metadata has no matching assistant item")
    if kind == "message":
        if target.get("role") != "assistant":
            raise ConversionError("OpenAI resume metadata must describe an assistant message")
        target["content"] = [
            {"type": "output_text", "text": part["text"], "annotations": []} for part in target["content"]
        ]
        phase = value.get("phase")
        if phase is not None:
            if phase not in {"commentary", "final_answer"}:
                raise ConversionError("Unsupported assistant phase")
            target["phase"] = phase
    elif kind != "function_call":
        raise ConversionError("Unsupported OpenAI resume item")
    target["id"] = _string(value.get("id"), "resume item id")
    status = value.get("status", "completed")
    if status not in {"completed", "incomplete"}:
        raise ConversionError("Unsupported resume item status")
    target["status"] = status


def _message_items(message: Mapping[str, Any], wire_names: Mapping[str, str]) -> list[dict[str, Any]]:
    role = message.get("role")
    if role not in {"user", "assistant", "system", "developer"}:
        raise ConversionError("Unsupported message role")
    items: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    function_metadata: list[Mapping[str, Any]] = []

    def flush() -> None:
        if pending:
            items.append({"type": "message", "role": role, "content": list(pending)})
            pending.clear()

    for block in _blocks(message.get("content")):
        kind = block.get("type")
        if role in {"system", "developer"} and kind != "text":
            raise ConversionError("System and developer messages must contain only text")
        if kind in {"text", "image"}:
            if role == "assistant" and kind == "image":
                raise ConversionError("Assistant image history is unsupported")
            pending.append(_input_part(block))
            continue
        flush()
        if kind == "tool_use" and role == "assistant":
            name = _string(block.get("name"), "tool name")
            items.append(
                {
                    "type": "function_call",
                    "call_id": _string(block.get("id"), "tool call id"),
                    "name": wire_names[name],
                    "arguments": json.dumps(dict(_object(block.get("input"), "tool input"))),
                }
            )
        elif kind == "tool_result" and role == "user":
            content = block.get("content", "")
            output = [_input_part(part) for part in _blocks(content)]
            if block.get("is_error"):
                output.insert(0, {"type": "input_text", "text": "Tool execution failed."})
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": _string(block.get("tool_use_id"), "tool result id"),
                    "output": output,
                }
            )
        elif kind == "redacted_thinking" and role == "assistant":
            state_kind, value = _read_state(block)
            if state_kind == "function_call":
                # The CLI may move opaque blocks ahead of parallel tool calls.
                # Bind by call_id after every tool in this message exists.
                function_metadata.append(value)
            else:
                _restore_state(items, state_kind, value)
        else:
            raise ConversionError("Unsupported Messages content; start a new session when changing providers")
    flush()
    for metadata in function_metadata:
        _restore_state(items, "function_call", metadata)
    return items


def to_responses_request(body: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Convert a request and return the wire-name -> original-name mapping.

    ``store=False`` makes caller-owned history, including encrypted reasoning,
    authoritative. Tool schemas explicitly disable Responses strict coercion.
    """
    _reject_extra(body, _REQUEST_KEYS, "Messages request")
    if body.get("context_management"):
        context = _object(body["context_management"], "context management")
        _reject_extra(context, {"edits"}, "context management")
        for edit in _list(context.get("edits", []), "context edits"):
            if edit != {"type": "clear_thinking_20251015", "keep": "all"}:
                raise ConversionError("Unsupported context management edit")
    mapping = _names(body)
    wire_names = {original: wire for wire, original in mapping.items()}
    payload: dict[str, Any] = {
        "model": _string(body.get("model"), "model"),
        "store": False,
        "include": ["reasoning.encrypted_content"],
        "input": [],
    }
    if "system" in body:
        system = _blocks(body["system"])
        if any(block.get("type") != "text" for block in system):
            raise ConversionError("Only text system prompts are supported")
        payload["instructions"] = "\n\n".join(_string(block.get("text"), "system text") for block in system)
    for message in _list(body.get("messages"), "messages"):
        payload["input"].extend(_message_items(_object(message, "message"), wire_names))
    if "max_tokens" in body:
        max_tokens = body["max_tokens"]
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
            raise ConversionError("max_tokens must be a positive integer")
        payload["max_output_tokens"] = max_tokens
    for key in ("stream", "temperature", "top_p"):
        if key in body:
            payload[key] = body[key]
    tools = []
    for raw in _list(body.get("tools", []), "tools"):
        tool = _object(raw, "tool")
        _reject_extra(tool, {"type", "name", "description", "input_schema", "cache_control", "strict"}, "tool")
        if tool.get("type") not in (None, "custom"):
            raise ConversionError("Only client-executed function tools are supported")
        tools.append(
            {
                "type": "function",
                "name": wire_names[tool["name"]],
                "description": _string(tool.get("description", ""), "tool description"),
                "parameters": dict(_object(tool.get("input_schema"), "tool schema")),
                "strict": False,
            }
        )
    if tools:
        payload["tools"] = tools
    if "tool_choice" in body:
        choice = _object(body["tool_choice"], "tool choice")
        kind = choice.get("type")
        if kind == "tool":
            name = choice.get("name")
            if name not in wire_names:
                raise ConversionError("Selected tool is not declared")
            payload["tool_choice"] = {"type": "function", "name": wire_names[name]}
        elif kind in {"auto", "none", "any"}:
            payload["tool_choice"] = "required" if kind == "any" else kind
        else:
            raise ConversionError("Unsupported tool choice")
        if "disable_parallel_tool_use" in choice:
            payload["parallel_tool_calls"] = not choice["disable_parallel_tool_use"]
    thinking = _object(body.get("thinking", {}), "thinking")
    config = _object(body.get("output_config", {}), "output config")
    _reject_extra(config, {"effort"}, "output config")
    _reject_extra(thinking, {"type", "budget_tokens", "display"}, "thinking")
    # Display controls presentation only; encrypted reasoning always round-trips.
    if thinking.get("display") not in (None, "updates", "summarized", "omitted"):
        raise ConversionError("Unsupported thinking display mode")
    thinking_type = thinking.get("type")
    if thinking_type not in (None, "enabled", "disabled", "adaptive"):
        raise ConversionError("Unsupported thinking mode")
    effort = config.get("effort")
    if effort is None and thinking_type in {"enabled", "adaptive"}:
        effort = "medium"
    if effort is None and thinking_type == "disabled":
        effort = "none"
    if effort is not None:
        if effort not in {"none", "minimal", "low", "medium", "high", "max", "xhigh"}:
            raise ConversionError("Unsupported reasoning effort")
        model = payload["model"]
        if model == "gpt-6.1-sol" or model.startswith("gpt-6.1-sol-"):
            # Sol always reasons; low is its closest supported mode to disabled/minimal.
            effort = "low" if effort in {"none", "minimal"} else effort
        elif effort == "max":
            effort = "xhigh"
        payload["reasoning"] = {"effort": effort}
    return payload, mapping


def anthropic_usage(usage: Mapping[str, Any] | None) -> dict[str, int]:
    """Responses input is inclusive of cached tokens; Messages input is not."""
    usage = usage or {}
    details = usage.get("input_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    created = int(details.get("cache_write_tokens") or 0)
    return {
        "input_tokens": max(0, int(usage.get("input_tokens") or 0) - cached - created),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": created,
    }


def _item_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    metadata = {"id": _string(item.get("id"), "output item id")}
    if item.get("type") == "function_call":
        metadata["call_id"] = _string(item.get("call_id"), "function call id")
    if item.get("status") is not None:
        metadata["status"] = item["status"]
    if item.get("phase") is not None:
        metadata["phase"] = item["phase"]
    return _state_block(str(item["type"]), metadata)


def _tool_block(item: Mapping[str, Any], mapping: Mapping[str, str]) -> dict[str, Any]:
    name = _string(item.get("name"), "function name")
    if name not in mapping:
        raise ConversionError("OpenAI returned an undeclared function")
    try:
        arguments = json.loads(_string(item.get("arguments"), "function arguments"))
    except ValueError as exc:
        raise ConversionError("OpenAI returned invalid function arguments") from exc
    return {
        "type": "tool_use",
        "id": _string(item.get("call_id"), "function call id"),
        "name": mapping[name],
        "input": dict(_object(arguments, "function arguments")),
    }


def _output_blocks(item: Mapping[str, Any], mapping: Mapping[str, str]) -> list[dict[str, Any]]:
    kind = item.get("type")
    if kind == "reasoning":
        return [_state_block("reasoning", _reasoning_item(item))]
    if kind == "function_call":
        return [_tool_block(item, mapping), _item_metadata(item)]
    if kind == "message":
        blocks = []
        for part in _list(item.get("content"), "output content"):
            part = _object(part, "output content part")
            if part.get("type") not in {"output_text", "refusal"}:
                raise ConversionError("Unsupported OpenAI output content")
            text = part.get("refusal") if part.get("type") == "refusal" else part.get("text")
            blocks.append({"type": "text", "text": _string(text, "output text")})
        if blocks:
            blocks.append(_item_metadata(item))
        return blocks
    raise ConversionError("Unsupported OpenAI output item")


def _stop_reason(response: Mapping[str, Any]) -> str:
    if response.get("status") == "incomplete":
        reason = (response.get("incomplete_details") or {}).get("reason")
        if reason == "max_output_tokens":
            return "max_tokens"
        if reason == "content_filter":
            return "refusal"
        raise ConversionError("OpenAI returned an unsupported incomplete response")
    output = response.get("output") or []
    if any(item.get("type") == "function_call" for item in output):
        return "tool_use"
    if any(part.get("type") == "refusal" for item in output for part in item.get("content", [])):
        return "refusal"
    return "end_turn"


def _error_event() -> dict[str, Any]:
    return {"type": "error", "error": {"type": "api_error", "message": "OpenAI did not complete the response."}}


def from_responses_response(
    response: Mapping[str, Any], name_mapping: Mapping[str, str], *, model: str | None = None
) -> dict[str, Any]:
    """Convert a final response; failed/cancelled responses become error envelopes."""
    if response.get("error") or response.get("status") in {"failed", "cancelled"}:
        return _error_event()
    if response.get("status") not in {"completed", "incomplete"}:
        raise ConversionError("OpenAI response is not terminal")
    content = []
    for item in _list(response.get("output"), "response output"):
        content.extend(_output_blocks(_object(item, "output item"), name_mapping))
    return {
        "id": "msg_" + _string(response.get("id"), "response id"),
        "type": "message",
        "role": "assistant",
        "model": model or response.get("model"),
        "content": content,
        "stop_reason": _stop_reason(response),
        "stop_sequence": None,
        "usage": anthropic_usage(response.get("usage")),
    }


class ResponsesStreamTranslator:
    """Translate parsed Responses SSE events into Messages SSE payloads.

    Call ``finish()`` after EOF: it rejects a truncated upstream stream rather
    than reporting an apparently successful assistant turn.
    """

    def __init__(self, name_mapping: Mapping[str, str], model: str):
        self.name_mapping = name_mapping
        self.model = model
        self._started = False
        self._finished = False
        self._next_index = 0
        self._blocks: dict[tuple[int, int], dict[str, Any]] = {}
        self._done_items: set[int] = set()

    def _start(self, response: Mapping[str, Any]) -> list[dict[str, Any]]:
        if self._started:
            return []
        self._started = True
        return [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_" + _string(response.get("id"), "response id"),
                    "type": "message",
                    "role": "assistant",
                    "model": self.model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": anthropic_usage(response.get("usage")),
                },
            }
        ]

    def _open(self, key: tuple[int, int], block: dict[str, Any]) -> list[dict[str, Any]]:
        if key in self._blocks:
            return []
        index = self._next_index
        self._next_index += 1
        self._blocks[key] = {"index": index, "text": "", "closed": False}
        return [{"type": "content_block_start", "index": index, "content_block": block}]

    def _delta(self, key: tuple[int, int], text: str, *, tool: bool = False) -> list[dict[str, Any]]:
        state = self._blocks[key]
        if state["closed"]:
            raise ConversionError("OpenAI sent content after its block closed")
        state["text"] += text
        delta = {"type": "input_json_delta", "partial_json": text} if tool else {"type": "text_delta", "text": text}
        return [{"type": "content_block_delta", "index": state["index"], "delta": delta}] if text else []

    def _complete(self, key: tuple[int, int], text: str, *, tool: bool = False) -> list[dict[str, Any]]:
        state = self._blocks[key]
        if not text.startswith(state["text"]):
            raise ConversionError("OpenAI completed content differs from streamed content")
        events = self._delta(key, text[len(state["text"]) :], tool=tool) if not state["closed"] else []
        if not state["closed"]:
            events.append({"type": "content_block_stop", "index": state["index"]})
            state["closed"] = True
        return events

    def _opaque(self, block: dict[str, Any]) -> list[dict[str, Any]]:
        index = self._next_index
        self._next_index += 1
        return [
            {"type": "content_block_start", "index": index, "content_block": block},
            {"type": "content_block_stop", "index": index},
        ]

    def _tool_start(self, output_index: int, item: Mapping[str, Any]) -> list[dict[str, Any]]:
        name = _string(item.get("name"), "function name")
        if name not in self.name_mapping:
            raise ConversionError("OpenAI returned an undeclared function")
        return self._open(
            (output_index, -1),
            {"type": "tool_use", "id": item.get("call_id"), "name": self.name_mapping[name], "input": {}},
        )

    def _finish_item(self, output_index: int, item: Mapping[str, Any]) -> list[dict[str, Any]]:
        if output_index in self._done_items:
            return []
        events = []
        kind = item.get("type")
        if kind == "function_call":
            _tool_block(item, self.name_mapping)
            events.extend(self._tool_start(output_index, item))
            events.extend(self._complete((output_index, -1), item["arguments"], tool=True))
            events.extend(self._opaque(_item_metadata(item)))
        elif kind == "message":
            for content_index, part in enumerate(item.get("content", [])):
                if part.get("type") not in {"output_text", "refusal"}:
                    raise ConversionError("Unsupported OpenAI output content")
                text = _string(
                    part.get("refusal") if part.get("type") == "refusal" else part.get("text"), "output text"
                )
                key = (output_index, content_index)
                events.extend(self._open(key, {"type": "text", "text": ""}))
                events.extend(self._complete(key, text))
            if item.get("content"):
                events.extend(self._opaque(_item_metadata(item)))
        elif kind == "reasoning":
            events.extend(self._opaque(_state_block("reasoning", _reasoning_item(item))))
        else:
            raise ConversionError("Unsupported OpenAI output item")
        self._done_items.add(output_index)
        return events

    def feed(self, event: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Consume one parsed event; each returned dict is an Anthropic SSE data payload."""
        if self._finished:
            raise ConversionError("OpenAI sent events after the response ended")
        kind = event.get("type")
        if kind in {"response.created", "response.in_progress"}:
            return self._start(_object(event.get("response"), "response"))
        if kind in {"error", "response.failed", "response.cancelled"}:
            self._finished = True
            return [_error_event()]
        if kind in {"response.completed", "response.incomplete"}:
            response = _object(event.get("response"), "response")
            if response.get("error"):
                self._finished = True
                return [_error_event()]
            events = self._start(response)
            for index, item in enumerate(response.get("output", [])):
                events.extend(self._finish_item(index, item))
            events.append(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": _stop_reason(response), "stop_sequence": None},
                    "usage": anthropic_usage(response.get("usage")),
                }
            )
            events.append({"type": "message_stop"})
            self._finished = True
            return events
        if not self._started:
            raise ConversionError("OpenAI stream did not begin with a response")
        if kind == "response.output_item.added":
            item = _object(event.get("item"), "output item")
            if item.get("type") == "function_call":
                return self._tool_start(event["output_index"], item)
            if item.get("type") not in {"message", "reasoning"}:
                raise ConversionError("Unsupported OpenAI output item")
        elif kind == "response.output_item.done":
            return self._finish_item(event["output_index"], _object(event.get("item"), "output item"))
        elif kind in {"response.output_text.delta", "response.refusal.delta"}:
            key = (event["output_index"], event["content_index"])
            return [
                *self._open(key, {"type": "text", "text": ""}),
                *self._delta(key, _string(event.get("delta"), "text delta")),
            ]
        elif kind == "response.function_call_arguments.delta":
            key = (event["output_index"], -1)
            if key not in self._blocks:
                raise ConversionError("OpenAI function arguments arrived before the tool call")
            return self._delta(key, _string(event.get("delta"), "arguments delta"), tool=True)
        elif kind == "response.content_part.added":
            part = _object(event.get("part"), "content part")
            if part.get("type") not in {"output_text", "refusal"}:
                raise ConversionError("Unsupported OpenAI content part")
            return self._open((event["output_index"], event["content_index"]), {"type": "text", "text": ""})
        return []

    def finish(self) -> list[dict[str, Any]]:
        """Verify upstream EOF followed a terminal event, including an explicit error."""
        if not self._finished:
            raise ConversionError("OpenAI response stream ended before completion")
        return []
