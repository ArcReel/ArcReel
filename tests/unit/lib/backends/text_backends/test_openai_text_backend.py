import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import get_provider_spec
from lib.backends.text_backends.base import (
    ImageInput,
    RequestTokens,
    TextCapability,
    TextGenerationRequest,
    TextOutputTruncatedError,
)
from lib.backends.text_backends.openai import OpenAITextBackend
from lib.config.registry import PROVIDER_REGISTRY
from lib.video_runtime.builtin import build_builtin_backend
from tests.custom_backend_support import create_custom_backend
from tests.fakes import bounded_poll_clock
from tests.http_capture import PathPrefixRewrite, capture_http, only_request, request_json

USAGE = {
    "prompt_tokens": 10,
    "completion_tokens": 8,
    "prompt_tokens_details": {"cached_tokens": 4},
    "completion_tokens_details": {"reasoning_tokens": 3},
}
TOKENS = RequestTokens(input_tokens=10, output_tokens=8, cache_read_tokens=4)
URL = "https://api.openai.com/v1/chat/completions"


def frame(delta, finish=None):
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def data(frame):
    return f"data: {json.dumps(frame)}\n\n".encode()


def sse(*frames):
    return httpx.Response(
        200, content=b"".join(map(data, frames)) + b"data: [DONE]\n\n", headers={"content-type": "text/event-stream"}
    )


@pytest.mark.parametrize(
    ("provider", "url", "strict", "vision"),
    [
        ("openai", URL, True, True),
        ("custom-7", "https://relay.example/v1/chat/completions", True, True),
        ("dashscope", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", True, True),
        ("minimax", "https://api.minimaxi.com/v1/chat/completions", True, True),
        ("ark", "https://ark.cn-beijing.volces.com/api/v3/chat/completions", False, True),
        ("ark-agent-plan", "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions", False, True),
        ("agnes", "https://apihub.agnes-ai.com/v1/chat/completions", True, False),
    ],
)
async def test_chat_channels(provider, url, strict, vision):
    if provider == "openai":
        backend = OpenAITextBackend(api_key="key", model="model")
    elif provider == "custom-7":
        backend = create_custom_backend(
            provider=SimpleNamespace(provider_id=provider, base_url="https://relay.example", api_key="key"),
            model_id="model",
            endpoint="openai-chat",
        )
    else:
        config = LoadedConfig(
            credentials={"api_key": "key"}, provider_meta=PROVIDER_REGISTRY[provider], rate_limiter=None
        )
        backend = build_builtin_backend(get_provider_spec(provider, "text"), config, "model")
    assert (TextCapability.VISION in backend.capabilities) == vision
    observed = []
    progress = []
    with capture_http() as router:
        route = router.post(url).mock(
            return_value=sse(
                frame({"reasoning_content": "thought"}),
                frame({"content": "<think>hide</think>\n```json\n{}\n```"}, "stop"),
                {"choices": [], "usage": USAGE},
            )
        )
        result = await backend.generate(
            TextGenerationRequest(
                "prompt",
                system_prompt="system",
                images=[ImageInput(url="https://img.example/a")] if vision else None,
                response_schema={"type": "object"},
                max_output_tokens=42,
                on_progress=progress.append,
                on_usage=observed.append,
            )
        )
    request = only_request(route)
    assert request.headers["authorization"] == "Bearer key"
    body = request_json(request)
    assert body["stream"]
    assert body["stream_options"] == {"include_usage": True}
    assert body["messages"][0] == {"role": "system", "content": "system"}
    if vision:
        assert body["messages"][1]["content"][0]["image_url"]["url"] == "https://img.example/a"
    assert body["response_format"]["json_schema"].get("strict") is (True if strict else None)
    assert body["max_completion_tokens" if provider == "openai" else "max_tokens"] == 42
    assert result.text == "{}"
    assert result.provider == provider
    assert result.model == "model"
    assert observed == result.request_tokens() == [TOKENS]
    assert any(p.thinking and p.characters == 0 for p in progress)


@pytest.mark.parametrize("finish", ["length", None])
async def test_structured_partial_output_records_known_usage(finish):
    observed = []
    with capture_http() as router:
        route = router.post(URL).mock(
            return_value=sse(frame({"content": "{}"}, finish), {"choices": [], "usage": USAGE})
        )
        with pytest.raises(TextOutputTruncatedError if finish else RuntimeError):
            await OpenAITextBackend(api_key="k").generate(
                TextGenerationRequest("q", response_schema={"type": "object"}, on_usage=observed.append)
            )
    assert observed == [TOKENS]
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("error_body", "response_schema", "expected_text"),
    [
        ({"error": {"param": "stream_options", "code": "unsupported_parameter"}}, None, "ok"),
        ({"error": {"message": "Unknown parameter: stream_options"}}, None, "ok"),
        ({"error": {"message": "Unknown parameter: stream_options"}}, {"type": "object"}, "{}"),
    ],
)
async def test_gateway_rejects_usage_option(error_body, response_schema, expected_text):
    with capture_http() as router:
        route = router.post(URL).mock(
            side_effect=[
                httpx.Response(400, json=error_body),
                sse(frame({"content": expected_text}, "stop")),
            ]
        )
        result = await OpenAITextBackend(api_key="k").generate(
            TextGenerationRequest("q", response_schema=response_schema)
        )
    first, second = [request_json(call.request) for call in route.calls]
    assert first.pop("stream_options") == {"include_usage": True}
    assert first == second
    assert "max_tokens" not in second
    assert "max_completion_tokens" not in second
    assert result.text == expected_text
    assert result.request_tokens() == []


async def test_cancellation_preserves_partial_usage():
    waiting = asyncio.Event()

    class Partial(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield data({"choices": [], "usage": USAGE})
            waiting.set()
            await asyncio.Event().wait()

    observed = []
    backend = OpenAITextBackend(api_key="k", transport_layer=PathPrefixRewrite("/v1/", "/gateway/"))
    with capture_http() as router:
        route = router.post(URL.replace("/v1/", "/gateway/")).mock(
            return_value=httpx.Response(200, stream=Partial(), headers={"content-type": "text/event-stream"})
        )
        task = asyncio.create_task(backend.generate(TextGenerationRequest("q", on_usage=observed.append)))
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert route.call_count == 1
    assert observed == [TOKENS]


async def test_snapshot_disables_native_schema():
    from lib.custom_provider.model_facts import text_capability_snapshot
    from lib.video_runtime.text import AssembledTextBackend

    snapshot = text_capability_snapshot("model", {"structured_output": False}, entry=None, provider_id="dashscope")
    backend = AssembledTextBackend(OpenAITextBackend(api_key="k", provider_name="dashscope"), snapshot)
    with capture_http() as router:
        route = router.post(URL).mock(return_value=sse(frame({"content": "{}"}, "stop")))
        result = await backend.generate(TextGenerationRequest("q", response_schema={"type": "object"}))
    body = request_json(only_request(route))
    assert "response_format" not in body
    assert "JSON Schema" in body["messages"][-1]["content"]
    assert result.text == "{}"
    assert result.request_tokens() == []


async def test_status_retries_use_only_runtime_budget():
    from openai import RateLimitError

    with capture_http() as router, bounded_poll_clock(step=0.1):
        route = router.post(URL).mock(return_value=httpx.Response(429, json={"error": {"code": "rate_limit"}}))
        with pytest.raises(RateLimitError):
            await OpenAITextBackend(api_key="k").generate(TextGenerationRequest("q"))
    assert route.call_count == 3


async def test_gateway_wraps_schema_rejection_in_rate_limit():
    with capture_http() as router, bounded_poll_clock(step=0.1):
        route = router.post(URL).mock(
            side_effect=[
                httpx.Response(429, json={"error": {"param": "response_format.json_schema"}}),
                sse(frame({"content": "{}"}, "stop")),
            ]
        )
        result = await OpenAITextBackend(api_key="k").generate(
            TextGenerationRequest("q", response_schema={"type": "object"})
        )
    native, fallback = [request_json(call.request) for call in route.calls]
    assert "response_format" in native
    assert "response_format" not in fallback
    assert result.text == "{}"
