"""anthropic-messages 调用通道：经端点注册表构造，真实 anthropic SDK 发出的请求由 respx 捕获。"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel

from lib.backends.text_backends.base import ImageInput, TextGenerationRequest
from tests.custom_backend_support import create_custom_backend
from tests.fakes import PNG_BYTES
from tests.http_capture import capture_http, only_request, request_json

_MESSAGES_URL = "https://relay.example.com/anthropic/v1/messages"


class _Verdict(BaseModel):
    label: Literal["ok", "bad"]
    score: int


def _provider(base_url: str = "https://relay.example.com/anthropic") -> SimpleNamespace:
    return SimpleNamespace(provider_id="custom-7", base_url=base_url, api_key="sk-relay")


def _sse(text: str, *, usage: dict, output_tokens: int, stop_reason: str = "end_turn") -> httpx.Response:
    """按 Messages 流式协议拼一条只含单个文本块的回包。"""
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-x",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {**usage, "output_tokens": 1},
                },
            },
        ),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        ),
        (
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


@pytest.mark.parametrize(("requested", "sent"), [(None, 16384), (4000, 4000)])
async def test_structured_output_with_image_is_encoded_natively(tmp_path: Path, requested, sent):
    image = tmp_path / "ref.png"
    image.write_bytes(PNG_BYTES)
    backend = create_custom_backend(provider=_provider(), model_id="claude-x", endpoint="anthropic-messages")

    with capture_http() as router:
        route = router.post(_MESSAGES_URL).mock(
            return_value=_sse(
                '{"label": "ok", "score": 7}',
                usage={
                    "input_tokens": 100,
                    "cache_read_input_tokens": 30,
                    "cache_creation_input_tokens": 20,
                    "cache_creation": {"ephemeral_5m_input_tokens": 5, "ephemeral_1h_input_tokens": 15},
                },
                output_tokens=12,
            )
        )
        result = await backend.generate(
            TextGenerationRequest(
                prompt="判断这张图",
                response_schema=_Verdict,
                images=[ImageInput(path=image)],
                system_prompt="你是审片员",
                max_output_tokens=requested,
            )
        )

    request = only_request(route)
    assert request.headers["x-api-key"] == "sk-relay"
    body = request_json(request)
    assert body["model"] == "claude-x"
    assert body["max_tokens"] == sent
    assert body["system"] == "你是审片员"
    assert body["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(PNG_BYTES).decode(),
                    },
                },
                {"type": "text", "text": "判断这张图"},
            ],
        }
    ]
    assert body["output_config"] == {
        "format": {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "title": "_Verdict",
                "properties": {
                    "label": {"type": "string", "enum": ["ok", "bad"], "title": "Label"},
                    "score": {"type": "integer", "title": "Score"},
                },
                "required": ["label", "score"],
                "additionalProperties": False,
            },
        }
    }

    assert _Verdict.model_validate_json(result.text) == _Verdict(label="ok", score=7)
    assert result.model == "claude-x"
    assert (result.input_tokens, result.output_tokens) == (150, 12)
    assert (result.cache_read_tokens, result.cache_write_5m_tokens, result.cache_write_1h_tokens) == (30, 5, 15)


async def test_relay_ignoring_output_config_falls_back_to_prompt_injection():
    backend = create_custom_backend(provider=_provider(), model_id="claude-x", endpoint="anthropic-messages")

    with capture_http() as router:
        route = router.post(_MESSAGES_URL).mock(
            side_effect=[
                _sse("好的，结论是 ok。", usage={"input_tokens": 40}, output_tokens=8),
                _sse('```json\n{"label": "bad", "score": 2}\n```', usage={"input_tokens": 90}, output_tokens=15),
            ]
        )
        result = await backend.generate(TextGenerationRequest(prompt="判断", response_schema=_Verdict))

    assert route.call_count == 2
    fallback_body = request_json(route.calls[1].request)
    assert "output_config" not in fallback_body
    assert '"enum": ["ok", "bad"]' in fallback_body["messages"][0]["content"][-1]["text"]
    assert _Verdict.model_validate_json(result.text) == _Verdict(label="bad", score=2)
    assert (result.input_tokens, result.output_tokens) == (130, 23)


async def test_empty_base_url_goes_to_official_endpoint_not_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://leaked.example.com")
    backend = create_custom_backend(provider=_provider(base_url=""), model_id="claude-x", endpoint="anthropic-messages")

    with capture_http() as router:
        route = router.post("https://api.anthropic.com/v1/messages").mock(
            return_value=_sse("hi", usage={"input_tokens": 3}, output_tokens=1)
        )
        result = await backend.generate(TextGenerationRequest(prompt="hello"))

    assert route.call_count == 1
    assert result.text == "hi"


async def test_pasted_endpoint_suffix_matches_displayed_address_with_and_without_override():
    """base_url 整条复制成 …/v1/messages：默认请求不重复该段；有覆盖时相对原样值（docs/adr/0101），与界面灰显一致。"""
    provider = _provider(base_url=f"{_MESSAGES_URL}/")
    default = create_custom_backend(provider=provider, model_id="claude-x", endpoint="anthropic-messages")
    overridden = create_custom_backend(
        provider=provider, model_id="claude-x", endpoint="anthropic-messages", path_overrides={"generate": "/chat"}
    )

    with capture_http() as router:
        default_route = router.post(_MESSAGES_URL).mock(
            return_value=_sse("hi", usage={"input_tokens": 3}, output_tokens=1)
        )
        override_route = router.post(f"{_MESSAGES_URL}/chat").mock(
            return_value=_sse("hi", usage={"input_tokens": 3}, output_tokens=1)
        )
        await default.generate(TextGenerationRequest(prompt="hello"))
        await overridden.generate(TextGenerationRequest(prompt="hello"))

    assert default_route.call_count == 1
    assert override_route.call_count == 1


async def test_parseable_json_without_message_stop_is_not_replayed():
    backend = create_custom_backend(provider=_provider(), model_id="claude-x", endpoint="anthropic-messages")
    complete = _sse("{}", usage={"input_tokens": 10}, output_tokens=2)
    partial = complete.content.split(b"event: message_stop")[0]
    with capture_http() as router:
        route = router.post(_MESSAGES_URL).mock(
            return_value=httpx.Response(200, headers=complete.headers, content=partial)
        )
        with pytest.raises(RuntimeError, match="terminal"):
            await backend.generate(TextGenerationRequest(prompt="test", response_schema={"type": "object"}))
    assert route.call_count == 1


@pytest.mark.parametrize(
    "outcome",
    ["success", "truncated", "fallback_failed", "require_complete", "failed_without_usage", "interrupted_after_usage"],
)
async def test_stream_settles_completed_requests(db_factory, caplog, outcome):
    from anthropic import InternalServerError
    from sqlalchemy import select

    from lib.backends.providers import CallPurpose
    from lib.backends.text_backends.anthropic import AnthropicTextBackend
    from lib.backends.text_backends.base import StructuredOutputExhaustedError, TextOutputTruncatedError
    from lib.backends.text_generator import TextGenerator
    from lib.billing.ledger import Ledger
    from lib.db.models.api_call import ApiCall
    from lib.db.repositories.usage_repo import PROJECT_LEVEL_SEGMENT_KEY, UsageRepository
    from lib.infra.retry import bind_clock

    class _Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        async def sleep(self, delay):
            self.now += delay

    progress = []
    marker = "sensitive-model-output"

    def replay(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"]["connect"] == 15
        if outcome == "failed_without_usage":
            return httpx.Response(
                500, json={"type": "error", "error": {"type": "api_error", "message": "server error"}}
            )
        text = (
            marker if outcome == "fallback_failed" else '<think>hidden</think>```json\n{"score": 1,"label":"ok"}\n```'
        )
        response = _sse(
            text,
            usage={"input_tokens": 100},
            output_tokens=50,
            stop_reason="max_tokens" if outcome in ("truncated", "require_complete") else "end_turn",
        )

        if outcome == "interrupted_after_usage":
            partial = response.content.split(b"event: message_stop")[0]
            return httpx.Response(200, headers=response.headers, content=partial)
        return response

    model = "gemini-3-flash-preview"
    backend = AnthropicTextBackend(api_key="secret-key-marker", model=model)
    generator = TextGenerator(
        backend, Ledger(session_factory=db_factory), "gemini-aistudio", purpose=CallPurpose.SCRIPT_GENERATION
    )
    request = TextGenerationRequest(
        prompt="sensitive-request-marker",
        response_schema=None if outcome == "require_complete" else _Verdict,
        on_progress=progress.append,
    )
    with capture_http() as router, bind_clock(_Clock()), caplog.at_level("INFO"):
        route = router.post("https://api.anthropic.com/v1/messages").mock(side_effect=replay)
        if outcome == "success":
            result = await generator.generate(request, "demo")
            assert _Verdict.model_validate_json(result.text).score == 1
        else:
            error = {
                "fallback_failed": StructuredOutputExhaustedError,
                "failed_without_usage": InternalServerError,
                "interrupted_after_usage": RuntimeError,
            }.get(outcome, TextOutputTruncatedError)
            with pytest.raises(error):
                await generator.generate(request, "demo", require_complete=True)
    assert all(s not in caplog.text for s in (marker, request.prompt, "secret-key-marker"))
    if outcome == "failed_without_usage":
        assert route.call_count == 3
        assert [p.phase for p in progress] == ["waiting", "retrying", "waiting", "retrying", "waiting"]
        async with db_factory() as session:
            row = (await session.execute(select(ApiCall))).scalar_one()
            assert row.status == "failed"
            assert (row.input_tokens, row.output_tokens) == (None, None)
            assert row.cost_amount == 0.0
            assert await UsageRepository(session).get_actual_costs_by_segment("demo") == {}
        return
    assert [p.phase for p in progress][:2] == ["waiting", "receiving"]
    count = 3 if outcome == "fallback_failed" else 1
    assert route.call_count == count
    async with db_factory() as session:
        row = (await session.execute(select(ApiCall))).scalar_one()
        assert row.status == ("success" if outcome == "success" else "failed")
        assert (row.input_tokens, row.output_tokens) == (100 * count, 50 * count)
        assert row.cost_amount == pytest.approx(0.0002 * count)
        costs = await UsageRepository(session).get_actual_costs_by_segment("demo")
        assert costs[PROJECT_LEVEL_SEGMENT_KEY]["text"]["USD"] == pytest.approx(0.0002 * count)


async def test_missing_usage_and_thinking_events():
    from lib.backends.text_backends.anthropic import AnthropicTextBackend

    progress = []
    response = _sse("{}", usage={}, output_tokens=1)
    frames = ['event: ping\ndata: {"type": "ping"}']
    for frame in response.text.strip().split("\n\n"):
        name, raw = frame.split("\ndata: ")
        event = json.loads(raw)
        if event["type"] == "message_start":
            del event["message"]["usage"]
        if event["type"] == "message_delta":
            del event["usage"]
        if event["type"] == "content_block_start":
            frames.extend(
                f"event: {extra['type']}\ndata: {json.dumps(dict(extra, index=0))}"
                for extra in (
                    {
                        "type": "content_block_start",
                        "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                    },
                    {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "thought"}},
                    {"type": "content_block_stop"},
                )
            )
            frames.append('event: ping\ndata: {"type": "ping"}')
        if "index" in event:
            event["index"] = 1
        frames.append(f"{name}\ndata: {json.dumps(event)}")

    sse_payload = "\n\n".join(frames) + "\n\n"
    backend = AnthropicTextBackend(api_key="fake", model="claude")
    with capture_http() as router:
        router.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse_payload)
        )
        result = await backend.generate(
            TextGenerationRequest(prompt="q", response_schema={"type": "object"}, on_progress=progress.append)
        )
    assert result.text == "{}"
    assert result.request_tokens() == []
    assert [(p.phase, p.thinking, p.characters) for p in progress] == [
        ("waiting", False, 0),
        ("receiving", True, 0),
        ("receiving", False, 2),
    ]


@pytest.mark.parametrize("native", [False, True])
async def test_model_capability_and_schema_rejection_select_prompt_fallback(native):
    from lib.custom_provider.model_facts import text_capability_snapshot
    from lib.video_runtime.text import AssembledTextBackend

    backend = create_custom_backend(provider=_provider(), model_id="claude-x", endpoint="anthropic-messages")
    snapshot = text_capability_snapshot("claude-x", {"structured_output": native}, entry=None, provider_id="custom-7")
    with capture_http() as router:
        responses = [_sse('{"score":1,"label":"ok"}', usage={"input_tokens": 10}, output_tokens=2)]
        if native:
            responses.insert(
                0,
                httpx.Response(
                    400,
                    json={"type": "error", "error": {"type": "invalid_request_error", "message": "unsupported schema"}},
                ),
            )
        route = router.post(_MESSAGES_URL).mock(side_effect=responses)
        result = await AssembledTextBackend(backend, snapshot).generate(
            TextGenerationRequest(prompt="q", response_schema=_Verdict)
        )
    assert _Verdict.model_validate_json(result.text).score == 1
    assert ["output_config" in request_json(c.request) for c in route.calls] == ([True, False] if native else [False])
