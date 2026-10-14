"""openai-chat-image：经 Chat Completions（modalities 含 image）出图的调用通道。

按供应商模型经端点注册表构造调用通道，HTTP 层由 respx 在真实 SDK 外拦截。
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import httpx

from lib.backends.image_backends.base import ImageGenerationRequest, ReferenceImage
from lib.custom_provider.endpoints import get_endpoint_spec
from tests.http_capture import capture_http, only_request, request_json

_PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
_REF = b"\xff\xd8\xff" + b"fake-jpeg-ref"


def _provider() -> SimpleNamespace:
    return SimpleNamespace(provider_id="custom-7", api_key="sk-relay", base_url="https://relay.example.com")


def _chat_response(message: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": "gemini-3-pro-image",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}],
            "usage": {"prompt_tokens": 1290, "completion_tokens": 1300, "total_tokens": 2590},
        },
    )


async def test_image_to_image_sends_reference_and_saves_returned_image(tmp_path):
    ref = tmp_path / "ref.png"  # 扩展名与实际编码不符：data URI 按文件头取 MIME
    ref.write_bytes(_REF)
    out = tmp_path / "out" / "storyboard.png"
    backend = get_endpoint_spec("openai-chat-image").build_backend(_provider(), "gemini-3-pro-image")
    data_uri = "data:image/png;base64," + base64.b64encode(_PNG).decode()

    with capture_http() as router:
        route = router.post("https://relay.example.com/v1/chat/completions").mock(
            return_value=_chat_response(
                {"content": "", "images": [{"type": "image_url", "image_url": {"url": data_uri}}]}
            )
        )
        result = await backend.generate(
            ImageGenerationRequest(
                prompt="把图1的人物放进雨夜街景",
                output_path=out,
                reference_images=[ReferenceImage(path=str(ref))],
                aspect_ratio="16:9",
                image_size="2K",
            )
        )

    body = request_json(only_request(route))
    assert body["model"] == "gemini-3-pro-image"
    assert "image" in body["modalities"]
    assert body["image_config"] == {"aspect_ratio": "16:9", "image_size": "2K"}
    content = body["messages"][-1]["content"]
    assert {"type": "text", "text": "把图1的人物放进雨夜街景"} in content
    assert {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(_REF).decode()},
    } in content
    assert out.read_bytes() == _PNG
    assert result.image_path == out
    assert result.provider == "custom-7"
    assert result.model == "gemini-3-pro-image"


async def test_text_to_image_extracts_markdown_image_from_content(tmp_path):
    # 部分中转站不填 message.images，把图片以 Markdown 内联在 content 里返回
    out = tmp_path / "out.png"
    backend = get_endpoint_spec("openai-chat-image").build_backend(_provider(), "gemini-2.5-flash-image")
    markdown = "好的：\n![image](https://cdn.example.com/a.png)"

    with capture_http() as router:
        route = router.post("https://relay.example.com/v1/chat/completions").mock(
            return_value=_chat_response({"content": markdown})
        )
        router.get("https://cdn.example.com/a.png").mock(return_value=httpx.Response(200, content=_PNG))
        await backend.generate(ImageGenerationRequest(prompt="雨夜街景", output_path=out, aspect_ratio="9:16"))

    content = request_json(only_request(route))["messages"][-1]["content"]
    assert [part["type"] for part in content] == ["text"]
    assert out.read_bytes() == _PNG


async def test_download_failure_retries_download_only(tmp_path, poll_clock):
    # 出图已计费：取件的瞬态失败只在下载层重试，不重发 Chat Completions 请求
    out = tmp_path / "out.png"
    backend = get_endpoint_spec("openai-chat-image").build_backend(_provider(), "gemini-2.5-flash-image")

    with capture_http() as router:
        route = router.post("https://relay.example.com/v1/chat/completions").mock(
            return_value=_chat_response({"content": "![image](https://cdn.example.com/a.png)"})
        )
        download = router.get("https://cdn.example.com/a.png").mock(
            side_effect=[httpx.Response(503), httpx.Response(200, content=_PNG)]
        )
        await backend.generate(ImageGenerationRequest(prompt="雨夜街景", output_path=out, aspect_ratio="9:16"))

    assert route.call_count == 1
    assert download.call_count == 2
    assert out.read_bytes() == _PNG
