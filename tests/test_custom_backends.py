"""CustomTextBackend / CustomImageBackend / CustomVideoBackend / CustomAudioBackend 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from lib.audio_backends.base import AudioCapability, AudioSynthesisRequest, AudioSynthesisResult
from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ImageGenerationResult
from lib.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult
from lib.video_backends.base import VideoCapability, VideoGenerationRequest, VideoGenerationResult

# ---------------------------------------------------------------------------
# CustomTextBackend
# ---------------------------------------------------------------------------


class TestCustomTextBackend:
    def test_properties(self):
        delegate = AsyncMock()
        delegate.capabilities = {TextCapability.TEXT_GENERATION}
        backend = CustomTextBackend(provider_id="custom-3", delegate=delegate, model="deepseek-v3")

        assert backend.name == "custom-3"
        assert backend.model == "deepseek-v3"
        assert backend.capabilities == {TextCapability.TEXT_GENERATION}

    def test_capabilities_delegated(self):
        """capabilities 属性直接来自 delegate。"""
        delegate = AsyncMock()
        delegate.capabilities = {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }
        backend = CustomTextBackend(provider_id="my-provider", delegate=delegate, model="gpt-5.4")

        assert backend.capabilities is delegate.capabilities

    async def test_generate_delegates(self):
        expected_result = TextGenerationResult(
            text="hello world",
            provider="custom-3",
            model="deepseek-v3",
            input_tokens=10,
            output_tokens=5,
        )
        delegate = AsyncMock()
        delegate.generate = AsyncMock(return_value=expected_result)
        delegate.capabilities = {TextCapability.TEXT_GENERATION}

        backend = CustomTextBackend(provider_id="custom-3", delegate=delegate, model="deepseek-v3")
        request = TextGenerationRequest(prompt="Say hello")
        result = await backend.generate(request)

        assert result is expected_result
        delegate.generate.assert_awaited_once_with(request)

    async def test_generate_passes_request_unchanged(self):
        """generate() 不修改请求对象，原样传给 delegate。"""
        delegate = AsyncMock()
        delegate.generate = AsyncMock(return_value=TextGenerationResult(text="ok", provider="x", model="y"))
        delegate.capabilities = set()

        backend = CustomTextBackend(provider_id="p", delegate=delegate, model="m")
        request = TextGenerationRequest(prompt="test prompt", system_prompt="be helpful")
        await backend.generate(request)

        call_args = delegate.generate.call_args[0]
        assert call_args[0] is request


# ---------------------------------------------------------------------------
# CustomImageBackend
# ---------------------------------------------------------------------------


class TestCustomImageBackend:
    def test_properties(self):
        delegate = AsyncMock()
        delegate.capabilities = {ImageCapability.TEXT_TO_IMAGE}
        backend = CustomImageBackend(provider_id="custom-img", delegate=delegate, model="flux-1")

        assert backend.name == "custom-img"
        assert backend.model == "flux-1"
        assert backend.capabilities == {ImageCapability.TEXT_TO_IMAGE}

    def test_capabilities_delegated(self):
        delegate = AsyncMock()
        delegate.capabilities = {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
        backend = CustomImageBackend(provider_id="img-provider", delegate=delegate, model="dall-e-4")

        assert backend.capabilities is delegate.capabilities

    async def test_generate_delegates(self, tmp_path: Path):
        output_path = tmp_path / "output.png"
        expected_result = ImageGenerationResult(
            image_path=output_path,
            provider="custom-img",
            model="flux-1",
        )
        delegate = AsyncMock()
        delegate.generate = AsyncMock(return_value=expected_result)
        delegate.capabilities = {ImageCapability.TEXT_TO_IMAGE}

        backend = CustomImageBackend(provider_id="custom-img", delegate=delegate, model="flux-1")
        request = ImageGenerationRequest(prompt="A mountain landscape", output_path=output_path)
        result = await backend.generate(request)

        assert result is expected_result
        delegate.generate.assert_awaited_once_with(request)

    async def test_generate_passes_request_unchanged(self, tmp_path: Path):
        output_path = tmp_path / "img.png"
        delegate = AsyncMock()
        delegate.generate = AsyncMock(
            return_value=ImageGenerationResult(image_path=output_path, provider="x", model="y")
        )
        delegate.capabilities = set()

        backend = CustomImageBackend(provider_id="p", delegate=delegate, model="m")
        request = ImageGenerationRequest(prompt="test", output_path=output_path, aspect_ratio="16:9")
        await backend.generate(request)

        call_args = delegate.generate.call_args[0]
        assert call_args[0] is request


# ---------------------------------------------------------------------------
# CustomVideoBackend
# ---------------------------------------------------------------------------


class TestCustomVideoBackend:
    def test_properties(self):
        delegate = AsyncMock()
        delegate.capabilities = {VideoCapability.TEXT_TO_VIDEO}
        backend = CustomVideoBackend(provider_id="custom-vid", delegate=delegate, model="wan-pro")

        assert backend.name == "custom-vid"
        assert backend.model == "wan-pro"
        assert backend.capabilities == {VideoCapability.TEXT_TO_VIDEO}

    def test_capabilities_delegated(self):
        delegate = AsyncMock()
        delegate.capabilities = {VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}
        backend = CustomVideoBackend(provider_id="vid-provider", delegate=delegate, model="kling-v2")

        assert backend.capabilities is delegate.capabilities

    async def test_generate_delegates(self, tmp_path: Path):
        output_path = tmp_path / "output.mp4"
        expected_result = VideoGenerationResult(
            video_path=output_path,
            provider="custom-vid",
            model="wan-pro",
            duration_seconds=5,
        )
        delegate = AsyncMock()
        delegate.generate = AsyncMock(return_value=expected_result)
        delegate.capabilities = {VideoCapability.TEXT_TO_VIDEO}

        backend = CustomVideoBackend(provider_id="custom-vid", delegate=delegate, model="wan-pro")
        request = VideoGenerationRequest(prompt="A flying eagle", output_path=output_path)
        result = await backend.generate(request)

        assert result is expected_result
        delegate.generate.assert_awaited_once_with(request)

    async def test_generate_passes_request_unchanged(self, tmp_path: Path):
        output_path = tmp_path / "vid.mp4"
        delegate = AsyncMock()
        delegate.generate = AsyncMock(
            return_value=VideoGenerationResult(video_path=output_path, provider="x", model="y", duration_seconds=5)
        )
        delegate.capabilities = set()

        backend = CustomVideoBackend(provider_id="p", delegate=delegate, model="m")
        request = VideoGenerationRequest(prompt="test", output_path=output_path, duration_seconds=8)
        await backend.generate(request)

        call_args = delegate.generate.call_args[0]
        assert call_args[0] is request

    async def test_multiple_capabilities(self):
        delegate = AsyncMock()
        all_caps = {VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO, VideoCapability.GENERATE_AUDIO}
        delegate.capabilities = all_caps
        backend = CustomVideoBackend(provider_id="full-provider", delegate=delegate, model="veo-3")

        assert backend.capabilities == all_caps


# ---------------------------------------------------------------------------
# CustomAudioBackend
# ---------------------------------------------------------------------------


class TestCustomAudioBackend:
    def test_properties(self):
        delegate = AsyncMock()
        delegate.capabilities = {AudioCapability.TEXT_TO_SPEECH}
        backend = CustomAudioBackend(provider_id="custom-9", delegate=delegate, model="tts-1")

        assert backend.name == "custom-9"
        assert backend.model == "tts-1"
        assert backend.capabilities == {AudioCapability.TEXT_TO_SPEECH}

    def test_capabilities_delegated(self):
        delegate = AsyncMock()
        delegate.capabilities = {AudioCapability.TEXT_TO_SPEECH}
        backend = CustomAudioBackend(provider_id="audio-provider", delegate=delegate, model="speech-1.5")

        assert backend.capabilities is delegate.capabilities

    async def test_synthesize_delegates(self, tmp_path: Path):
        output_path = tmp_path / "out.wav"
        expected_result = AudioSynthesisResult(
            provider="custom-9",
            model="tts-1",
            characters=4,
            output_path=output_path,
        )
        delegate = AsyncMock()
        delegate.synthesize = AsyncMock(return_value=expected_result)
        delegate.capabilities = {AudioCapability.TEXT_TO_SPEECH}

        backend = CustomAudioBackend(provider_id="custom-9", delegate=delegate, model="tts-1")
        request = AudioSynthesisRequest(text="你好世界", output_path=output_path, voice="alloy")
        result = await backend.synthesize(request)

        assert result is expected_result
        delegate.synthesize.assert_awaited_once_with(request)

    async def test_synthesize_passes_request_unchanged(self, tmp_path: Path):
        output_path = tmp_path / "seg.wav"
        delegate = AsyncMock()
        delegate.synthesize = AsyncMock(
            return_value=AudioSynthesisResult(provider="x", model="y", characters=2, output_path=output_path)
        )
        delegate.capabilities = set()

        backend = CustomAudioBackend(provider_id="p", delegate=delegate, model="m")
        request = AudioSynthesisRequest(text="hi", output_path=output_path, voice="alloy", speed=1.2)
        await backend.synthesize(request)

        call_args = delegate.synthesize.call_args[0]
        assert call_args[0] is request
