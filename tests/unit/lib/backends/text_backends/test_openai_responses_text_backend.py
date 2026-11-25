"""OpenAIResponsesTextBackend 单元测试：经端点注册表构造，HTTP 层用 respx 替身。"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import BadRequestError
from pydantic import BaseModel

from lib.backends.text_backends.base import (
    ImageInput,
    TextGenerationRequest,
    TextOutputTruncatedError,
    resolve_schema,
)
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY
from tests.http_capture import capture_http, only_request, request_json

_RESPONSES_URL = "https://relay.test/v1/responses"


class _Overview(BaseModel):
    title: str
    episode_count: int


def _backend(model: str = "gpt-5.5-pro"):
    provider = SimpleNamespace(base_url="https://relay.test", api_key="sk-test", provider_id="custom-1")
    return ENDPOINT_REGISTRY["openai-responses"].build_backend(provider, model)


def _response(
    text: str,
    *,
    input_tokens: int = 100,
    cached_tokens: int = 0,
    output_tokens: int = 20,
    incomplete_reason: str | None = None,
    error: str | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "status": "failed" if error else "incomplete" if incomplete_reason else "completed",
        "error": {"code": "server_error", "message": error} if error else None,
        "incomplete_details": {"reason": incomplete_reason} if incomplete_reason else None,
        "model": "gpt-5.5-pro",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": cached_tokens},
            "output_tokens": output_tokens,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": input_tokens + output_tokens,
        },
    }
    return _sse(
        {"type": "response.output_text.delta", "delta": text},
        {"type": "response." + body["status"], "response": body},
    )


def _sse(*events):
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text="".join("data: " + json.dumps(event) + "\n\n" for event in events),
    )


async def test_structured_output_encodes_text_format(tmp_path):
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
    img_path = tmp_path / "ref.png"
    img_path.write_bytes(png_bytes)
    payload = json.dumps({"title": "雪夜", "episode_count": 3}, ensure_ascii=False)

    with capture_http() as router:
        route = router.post(_RESPONSES_URL).mock(
            return_value=_response(payload, input_tokens=1200, cached_tokens=1000, output_tokens=40)
        )
        result = await _backend().generate(
            TextGenerationRequest(
                prompt="概括这部小说",
                response_schema=_Overview,
                images=[ImageInput(path=img_path)],
                system_prompt="你是编剧",
                max_output_tokens=2000,
            )
        )

    body = request_json(only_request(route))
    assert body["text"] == {"format": {"type": "json_schema", "name": "response", "schema": resolve_schema(_Overview)}}
    assert body["stream"] is True
    assert body["instructions"] == "你是编剧"
    assert body["max_output_tokens"] == 2000
    assert body["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(png_bytes).decode()},
                {"type": "input_text", "text": "概括这部小说"},
            ],
        }
    ]
    assert json.loads(result.text) == {"title": "雪夜", "episode_count": 3}
    assert (result.input_tokens, result.cache_read_tokens, result.output_tokens) == (1200, 1000, 40)


async def test_schema_violating_output_falls_back_to_prompt_injection():
    with capture_http() as router:
        route = router.post(_RESPONSES_URL).mock(
            side_effect=[
                _response("好的，下面是概括……", input_tokens=100, output_tokens=10),
                _response('```json\n{"title": "雪夜", "episode_count": 3}\n```', input_tokens=150, output_tokens=12),
            ]
        )
        result = await _backend().generate(TextGenerationRequest(prompt="概括这部小说", response_schema=_Overview))

    assert route.call_count == 2
    fallback_body = request_json(route.calls[1].request)
    assert "text" not in fallback_body
    assert '"episode_count"' in fallback_body["input"][0]["content"]
    assert json.loads(result.text) == {"title": "雪夜", "episode_count": 3}
    assert (result.input_tokens, result.output_tokens) == (250, 22)


@pytest.mark.parametrize(
    "error",
    [
        {"code": "invalid_json_schema"},
        {"param": "text.format.schema"},
        {"message": "Invalid request"},
    ],
)
async def test_rejected_text_format_falls_back_to_prompt_injection(error):
    with capture_http() as router:
        route = router.post(_RESPONSES_URL).mock(
            side_effect=[
                httpx.Response(400, json={"error": error}),
                _response('{"title": "雪夜", "episode_count": 3}'),
            ]
        )
        result = await _backend().generate(TextGenerationRequest(prompt="概括这部小说", response_schema=_Overview))

    assert route.call_count == 2
    assert "text" not in request_json(route.calls[1].request)
    assert json.loads(result.text) == {"title": "雪夜", "episode_count": 3}


async def test_non_schema_rejection_propagates_even_when_message_mentions_schema():
    with capture_http() as router:
        route = router.post(_RESPONSES_URL).respond(
            400, json={"error": {"param": "input", "message": "Invalid input with json_schema"}}
        )
        with pytest.raises(BadRequestError):
            await _backend().generate(TextGenerationRequest(prompt="概括", response_schema=_Overview))
    assert route.call_count == 1


async def test_truncated_structured_output_raises():
    with capture_http() as router:
        router.post(_RESPONSES_URL).mock(return_value=_response('{"title": "雪', incomplete_reason="max_output_tokens"))
        with pytest.raises(TextOutputTruncatedError):
            await _backend().generate(TextGenerationRequest(prompt="概括这部小说", response_schema=_Overview))


async def test_truncated_free_text_is_flagged(caplog):
    with capture_http() as router, caplog.at_level("WARNING", logger="lib.backends.text_backends.base"):
        router.post(_RESPONSES_URL).mock(return_value=_response("从前有座山", incomplete_reason="max_output_tokens"))
        result = await _backend().generate(TextGenerationRequest(prompt="讲个故事"))

    assert result.text == "从前有座山"
    assert result.truncated is True
    assert any("max_output_tokens" in record.message for record in caplog.records)


async def test_failed_status_raises():
    with capture_http() as router:
        router.post(_RESPONSES_URL).mock(return_value=_response("", error="upstream boom"))
        with pytest.raises(RuntimeError, match="failed"):
            await _backend().generate(TextGenerationRequest(prompt="讲个故事"))


@pytest.mark.parametrize("cancel", [False, True])
async def test_disconnected_json_keeps_reported_usage_once(cancel):
    billed = []
    read_pending = asyncio.Event()

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _sse(
                {
                    "type": "response.in_progress",
                    "response": {
                        "usage": {"input_tokens": 7, "output_tokens": 2, "input_tokens_details": {"cached_tokens": 3}},
                    },
                },
                {"type": "response.output_text.delta", "delta": "{}"},
            ).content
            if cancel:
                read_pending.set()
                await asyncio.Event().wait()

    with capture_http() as router:
        route = router.post(_RESPONSES_URL).mock(
            return_value=httpx.Response(200, stream=Body(), headers={"content-type": "text/event-stream"})
        )
        task = asyncio.create_task(
            _backend().generate(TextGenerationRequest(prompt="hi", response_schema={}, on_usage=billed.append))
        )
        if cancel:
            async with asyncio.timeout(5):
                await read_pending.wait()
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await task
    assert route.call_count == 1
    assert [(u.input_tokens, u.output_tokens, u.cache_read_tokens) for u in billed] == [(7, 2, 3)]


async def test_unmetered_completion_counts_only_text_deltas():
    progress, billed = [], []
    with capture_http() as router:
        route = router.post(_RESPONSES_URL).mock(
            return_value=_sse(
                {"type": "response.reasoning_summary_text.delta", "delta": "thinking"},
                {"type": "response.output_text.delta", "delta": "hi"},
                {"type": "response.completed", "response": {"status": "completed", "output": [], "usage": None}},
            )
        )
        result = await _backend().generate(
            TextGenerationRequest(prompt="hi", on_usage=billed.append, on_progress=progress.append)
        )
    assert result.text == "hi"
    assert result.request_tokens() == billed == []
    assert [(p.characters, p.thinking) for p in progress if p.phase == "receiving"] == [(0, True), (2, False)]
    assert "max_output_tokens" not in request_json(only_request(route))


@pytest.mark.parametrize(
    ("status", "details", "output"),
    [
        ("incomplete", {"reason": "content_filter"}, []),
        ("completed", None, [{"type": "message", "content": [{"type": "refusal", "refusal": "declined"}]}]),
        ("failed", None, []),
    ],
)
async def test_unsuccessful_generation_never_enters_schema_fallback(status, details, output):
    billed = []
    with capture_http() as router:
        route = router.post(_RESPONSES_URL).mock(
            return_value=_sse(
                {
                    "type": "response." + status,
                    "response": {
                        "status": status,
                        "incomplete_details": details,
                        "output": output,
                        "usage": {"input_tokens": 10, "output_tokens": 1, "input_tokens_details": {"cached_tokens": 0}},
                    },
                },
            )
        )
        with pytest.raises(RuntimeError):
            await _backend().generate(TextGenerationRequest(prompt="hi", response_schema={}, on_usage=billed.append))
    assert route.call_count == 1
    assert len(billed) == 1
    assert billed[0].input_tokens == 10
