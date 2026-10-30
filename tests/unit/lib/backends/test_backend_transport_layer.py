"""自定义供应商可挂接的 Python 调用通道把注入的 transport 层交给自己的 HTTP 客户端。

路径覆盖（docs/adr/0101）在 transport 层改写请求：注入层改写过的路径就是实际发出的路径，
SDK（OpenAI、Anthropic、google-genai、方舟）与直连 httpx 的调用通道都一样。
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.audio_backends.base import AudioSynthesisRequest
from lib.backends.audio_backends.dashscope import DashScopeAudioBackend
from lib.backends.audio_backends.gemini import GeminiAudioBackend
from lib.backends.audio_backends.minimax import MiniMaxAudioBackend
from lib.backends.audio_backends.openai import OpenAIAudioBackend
from lib.backends.image_backends.ark import ArkImageBackend
from lib.backends.image_backends.base import ImageGenerationRequest
from lib.backends.image_backends.dashscope import DashScopeImageBackend
from lib.backends.image_backends.gemini import GeminiImageBackend
from lib.backends.image_backends.kling import KlingImageBackend
from lib.backends.image_backends.minimax import MiniMaxImageBackend
from lib.backends.image_backends.openai import OpenAIImageBackend
from lib.backends.image_backends.openai_chat import OpenAIChatImageBackend
from lib.backends.image_backends.openrouter import OpenRouterImageBackend
from lib.backends.text_backends.anthropic import AnthropicTextBackend
from lib.backends.text_backends.base import TextGenerationRequest
from lib.backends.text_backends.gemini import GeminiTextBackend
from lib.backends.text_backends.openai import OpenAITextBackend
from lib.backends.text_backends.openai_responses import OpenAIResponsesTextBackend
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.ark import ArkVideoBackend
from lib.backends.video_backends.dashscope import DashScopeVideoBackend
from lib.backends.video_backends.gemini import GeminiVideoBackend
from lib.backends.video_backends.kling import KlingVideoBackend
from lib.backends.video_backends.openai import OpenAIVideoBackend
from lib.backends.video_backends.vidu import ViduVideoBackend
from tests.fakes import bounded_poll_clock
from tests.http_capture import PathPrefixRewrite, capture_http

BASE = "https://relay.example"

Call = Callable[[TransportLayer, Path], Awaitable[object]]


def _text() -> TextGenerationRequest:
    return TextGenerationRequest(prompt="一只猫走过屋顶")


def _image(tmp_path: Path) -> ImageGenerationRequest:
    return ImageGenerationRequest(prompt="一只猫走过屋顶", output_path=tmp_path / "out.png")


def _video(tmp_path: Path) -> VideoGenerationRequest:
    return VideoGenerationRequest(prompt="一只猫走过屋顶", output_path=tmp_path / "out.mp4", duration_seconds=5)


def _speech(tmp_path: Path) -> AudioSynthesisRequest:
    return AudioSynthesisRequest(text="你好", output_path=tmp_path / "out.wav", voice="Cherry")


_CALLS: dict[str, Call] = {
    "openai-chat": lambda layer, _: OpenAITextBackend(
        api_key="k", base_url=f"{BASE}/v1", transport_layer=layer
    ).generate(_text()),
    "openai-responses": lambda layer, _: OpenAIResponsesTextBackend(
        api_key="k", base_url=f"{BASE}/v1", transport_layer=layer
    ).generate(_text()),
    "anthropic-messages": lambda layer, _: AnthropicTextBackend(
        api_key="k", model="claude-x", base_url=BASE, transport_layer=layer
    ).generate(_text()),
    "gemini-generate": lambda layer, _: GeminiTextBackend(api_key="k", base_url=BASE, transport_layer=layer).generate(
        _text()
    ),
    "openai-images": lambda layer, tmp_path: OpenAIImageBackend(
        api_key="k", base_url=f"{BASE}/v1", transport_layer=layer
    ).generate(_image(tmp_path)),
    "openai-chat-image": lambda layer, tmp_path: OpenAIChatImageBackend(
        api_key="k", model="m", base_url=f"{BASE}/v1", transport_layer=layer
    ).generate(_image(tmp_path)),
    "gemini-image": lambda layer, tmp_path: GeminiImageBackend(
        api_key="k", base_url=BASE, transport_layer=layer
    ).generate(_image(tmp_path)),
    "dashscope-image": lambda layer, tmp_path: DashScopeImageBackend(
        api_key="k", base_url=BASE, transport_layer=layer
    ).generate(_image(tmp_path)),
    "minimax-image": lambda layer, tmp_path: MiniMaxImageBackend(
        api_key="k", base_url=BASE, transport_layer=layer
    ).generate(_image(tmp_path)),
    "ark-seedream": lambda layer, tmp_path: ArkImageBackend(
        api_key="k", base_url=f"{BASE}/api/v3", transport_layer=layer
    ).generate(_image(tmp_path)),
    "kling-image": lambda layer, tmp_path: KlingImageBackend(
        auth_mode="bearer", api_key="k", base_url=f"{BASE}/v1", transport_layer=layer
    ).generate(_image(tmp_path)),
    "openrouter-images": lambda layer, tmp_path: OpenRouterImageBackend(
        api_key="k", base_url=f"{BASE}/api/v1", transport_layer=layer
    ).generate(_image(tmp_path)),
    "openai-tts": lambda layer, tmp_path: OpenAIAudioBackend(
        api_key="k", base_url=f"{BASE}/v1", model="tts-1", transport_layer=layer
    ).synthesize(_speech(tmp_path)),
    "dashscope-tts": lambda layer, tmp_path: DashScopeAudioBackend(
        api_key="k", base_url=BASE, transport_layer=layer
    ).synthesize(_speech(tmp_path)),
    "minimax-tts": lambda layer, tmp_path: MiniMaxAudioBackend(
        api_key="k", base_url=BASE, transport_layer=layer
    ).synthesize(_speech(tmp_path)),
    "gemini-tts": lambda layer, tmp_path: GeminiAudioBackend(
        api_key="k", base_url=BASE, transport_layer=layer
    ).synthesize(_speech(tmp_path)),
    "openai-video": lambda layer, tmp_path: OpenAIVideoBackend(
        api_key="k", model="sora-2", base_url=f"{BASE}/v1", transport_layer=layer
    ).generate(_video(tmp_path)),
    "gemini-video": lambda layer, tmp_path: GeminiVideoBackend(
        api_key="k", base_url=BASE, transport_layer=layer
    ).generate(_video(tmp_path)),
    "ark-seedance": lambda layer, tmp_path: ArkVideoBackend(
        api_key="k", model="doubao-seedance-1-5-pro-251215", base_url=f"{BASE}/api/v3", transport_layer=layer
    ).generate(_video(tmp_path)),
    "vidu-video": lambda layer, tmp_path: ViduVideoBackend(
        api_key="k", base_url=f"{BASE}/ent/v2", transport_layer=layer
    ).generate(_video(tmp_path)),
    "dashscope-async-video": lambda layer, tmp_path: DashScopeVideoBackend(
        api_key="k", base_url=BASE, transport_layer=layer
    ).generate(_video(tmp_path)),
    "kling-video": lambda layer, tmp_path: KlingVideoBackend(
        auth_mode="bearer", api_key="k", base_url=f"{BASE}/v1", transport_layer=layer
    ).generate(_video(tmp_path)),
}


@pytest.mark.parametrize("call", _CALLS.values(), ids=_CALLS.keys())
async def test_requests_leave_on_the_path_rewritten_by_the_injected_layer(call: Call, tmp_path: Path):
    layer = PathPrefixRewrite("/", "/rewritten/")
    with capture_http() as router, bounded_poll_clock():
        rewritten = router.route(host="relay.example", path__startswith="/rewritten/").mock(
            return_value=httpx.Response(400, json={"error": {"message": "stop here"}})
        )
        # 回包一律拒绝，各调用通道怎样报错不在本用例范围内，只看请求怎样发出
        with contextlib.suppress(Exception):
            await call(layer, tmp_path)

    assert layer.seen
    assert rewritten.call_count == len(layer.seen)
