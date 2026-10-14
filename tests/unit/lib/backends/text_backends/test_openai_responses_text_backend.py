"""OpenAIResponsesTextBackend 单元测试：经端点注册表构造，HTTP 层用 respx 替身。"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
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
    # host-only base_url：openai 族端点补 /v1 的接线一并覆盖
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
    return httpx.Response(200, json=body)


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
    # 用量契约未落地前按现有字段上报：input_tokens 是含缓存命中的总输入
    assert (result.input_tokens, result.output_tokens) == (1200, 40)


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
    # 降级仍走 Responses，不再带 text.format，schema 改写进 prompt
    assert "text" not in fallback_body
    assert '"episode_count"' in fallback_body["input"][0]["content"]
    assert json.loads(result.text) == {"title": "雪夜", "episode_count": 3}
    # 原生那次 200 调用已计费，并入降级结果
    assert (result.input_tokens, result.output_tokens) == (250, 22)


async def test_rejected_text_format_falls_back_to_prompt_injection():
    with capture_http() as router:
        route = router.post(_RESPONSES_URL).mock(
            side_effect=[
                httpx.Response(400, json={"error": {"message": "Invalid schema for response_format 'response'"}}),
                _response('{"title": "雪夜", "episode_count": 3}'),
            ]
        )
        result = await _backend().generate(TextGenerationRequest(prompt="概括这部小说", response_schema=_Overview))

    assert route.call_count == 2
    assert "text" not in request_json(route.calls[1].request)
    assert json.loads(result.text) == {"title": "雪夜", "episode_count": 3}


async def test_truncated_structured_output_raises():
    with capture_http() as router:
        router.post(_RESPONSES_URL).mock(return_value=_response('{"title": "雪', incomplete_reason="max_output_tokens"))
        with pytest.raises(TextOutputTruncatedError):
            await _backend().generate(TextGenerationRequest(prompt="概括这部小说", response_schema=_Overview))


async def test_truncated_free_text_is_flagged():
    with capture_http() as router:
        router.post(_RESPONSES_URL).mock(return_value=_response("从前有座山", incomplete_reason="max_output_tokens"))
        result = await _backend().generate(TextGenerationRequest(prompt="讲个故事"))

    assert result.text == "从前有座山"
    assert result.truncated is True


async def test_failed_status_raises():
    with capture_http() as router:
        router.post(_RESPONSES_URL).mock(return_value=_response("", error="upstream boom"))
        with pytest.raises(RuntimeError, match="upstream boom"):
            await _backend().generate(TextGenerationRequest(prompt="讲个故事"))
