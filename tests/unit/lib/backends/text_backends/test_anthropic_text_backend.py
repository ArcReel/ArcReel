"""anthropic-messages 调用通道：经端点注册表构造，真实 anthropic SDK 发出的请求由 respx 捕获。"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import httpx
from pydantic import BaseModel

from lib.backends.text_backends.base import ImageInput, TextGenerationRequest
from lib.custom_provider.factory import create_custom_backend
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


async def test_structured_output_with_image_is_encoded_natively(tmp_path: Path):
    image = tmp_path / "ref.png"
    image.write_bytes(PNG_BYTES)
    backend = create_custom_backend(provider=_provider(), model_id="claude-x", endpoint="anthropic-messages")

    with capture_http() as router:
        route = router.post(_MESSAGES_URL).mock(
            return_value=_sse(
                '{"label": "ok", "score": 7}',
                usage={"input_tokens": 100, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 20},
                output_tokens=12,
            )
        )
        result = await backend.generate(
            TextGenerationRequest(
                prompt="判断这张图",
                response_schema=_Verdict,
                images=[ImageInput(path=image)],
                system_prompt="你是审片员",
                max_output_tokens=64000,
            )
        )

    request = only_request(route)
    assert request.headers["x-api-key"] == "sk-relay"
    body = request_json(request)
    assert body["model"] == "claude-x"
    assert body["max_tokens"] == 64000
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
    # 现有字段口径：总输入 = 未缓存输入 + 缓存读 + 缓存写
    assert result.input_tokens == 150
    assert result.output_tokens == 12


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
    # 两次调用都已计费
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
