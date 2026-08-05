"""CustomTextBackend / CustomImageBackend / CustomVideoBackend / CustomAudioBackend 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.audio_backends.base import AudioCapability, AudioSynthesisRequest, AudioSynthesisResult
from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ImageGenerationResult
from lib.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult
from lib.video_backends.base import (
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
)

# ---------------------------------------------------------------------------
# CustomTextBackend
# ---------------------------------------------------------------------------


class TestCustomTextBackend:
    @pytest.mark.unit
    def test_properties(self):
        delegate = AsyncMock()
        delegate.capabilities = {TextCapability.TEXT_GENERATION}
        backend = CustomTextBackend(provider_id="custom-3", delegate=delegate, model="deepseek-v3")

        assert backend.name == "custom-3"
        assert backend.model == "deepseek-v3"
        assert backend.capabilities == {TextCapability.TEXT_GENERATION}

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
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
    @pytest.mark.unit
    def test_properties(self):
        delegate = AsyncMock()
        delegate.capabilities = {ImageCapability.TEXT_TO_IMAGE}
        backend = CustomImageBackend(provider_id="custom-img", delegate=delegate, model="flux-1")

        assert backend.name == "custom-img"
        assert backend.model == "flux-1"
        assert backend.capabilities == {ImageCapability.TEXT_TO_IMAGE}

    @pytest.mark.unit
    def test_capabilities_delegated(self):
        delegate = AsyncMock()
        delegate.capabilities = {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
        backend = CustomImageBackend(provider_id="img-provider", delegate=delegate, model="dall-e-4")

        assert backend.capabilities is delegate.capabilities

    @pytest.mark.unit
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

    @pytest.mark.unit
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
    @pytest.mark.unit
    def test_properties(self):
        delegate = AsyncMock()
        backend = CustomVideoBackend(provider_id="custom-vid", delegate=delegate, model="wan-pro")

        assert backend.name == "custom-vid"
        assert backend.model == "wan-pro"

    @pytest.mark.unit
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

        backend = CustomVideoBackend(provider_id="custom-vid", delegate=delegate, model="wan-pro")
        request = VideoGenerationRequest(prompt="A flying eagle", output_path=output_path)
        result = await backend.generate(request)

        assert result is expected_result
        delegate.generate.assert_awaited_once_with(request)

    @pytest.mark.unit
    async def test_generate_passes_request_unchanged(self, tmp_path: Path):
        output_path = tmp_path / "vid.mp4"
        delegate = AsyncMock()
        delegate.generate = AsyncMock(
            return_value=VideoGenerationResult(video_path=output_path, provider="x", model="y", duration_seconds=5)
        )

        backend = CustomVideoBackend(provider_id="p", delegate=delegate, model="m")
        request = VideoGenerationRequest(prompt="test", output_path=output_path, duration_seconds=8)
        await backend.generate(request)

        call_args = delegate.generate.call_args[0]
        assert call_args[0] is request

    @pytest.mark.unit
    def test_video_capabilities_for_tier_delegates_when_delegate_supports_it(self):
        """delegate 实现 tier-aware 查询（如 Kling）时，按请求档位透传其结果，而不是回落
        context-free 的 video_capabilities——否则 media_generator 的 getattr 探测会命中一个
        总是保守拒绝的空壳方法,起不到收窄效果。"""
        pro_caps = VideoCapabilities(first_frame=True, last_frame=True)
        delegate = AsyncMock()
        delegate.video_capabilities_for_tier = MagicMock(return_value=pro_caps)
        backend = CustomVideoBackend(provider_id="vid-provider", delegate=delegate, model="kling-v2-5-turbo")

        result = backend.video_capabilities_for_tier("pro", resolution="1080p")

        assert result is pro_caps
        delegate.video_capabilities_for_tier.assert_called_once_with("pro", resolution="1080p")

    @pytest.mark.unit
    def test_video_capabilities_for_tier_falls_back_without_tier_support(self):
        """delegate 未实现 tier-aware 查询时,回落到 context-free 的 video_capabilities。"""
        static_caps = VideoCapabilities(first_frame=True, last_frame=False)
        delegate = AsyncMock(spec=["video_capabilities", "capabilities"])
        delegate.video_capabilities = static_caps
        backend = CustomVideoBackend(provider_id="vid-provider", delegate=delegate, model="wan-pro")

        result = backend.video_capabilities_for_tier("pro")

        assert result is static_caps

    @pytest.mark.unit
    def test_video_capabilities_for_tier_ignores_full_injected_capabilities(self):
        """工厂注入的完整合成能力（`with_video_capabilities` 不带 overrides,如 factory.py 未
        配置用户覆盖时的调用形状）不得短路档位查询——那是 context-free 的系统判定,对 Kling 等
        档位敏感 backend 是保守声明;短路会让本方法在生产环境等价于未实现档位感知。"""
        static_merged = VideoCapabilities(first_frame=True, last_frame=False)
        pro_caps = VideoCapabilities(first_frame=True, last_frame=True)
        delegate = AsyncMock()
        delegate.video_capabilities_for_tier = MagicMock(return_value=pro_caps)
        backend = CustomVideoBackend(
            provider_id="vid-provider", delegate=delegate, model="kling-v2-5-turbo"
        ).with_video_capabilities(static_merged)

        result = backend.video_capabilities_for_tier("pro", resolution="1080p")

        assert result is pro_caps
        delegate.video_capabilities_for_tier.assert_called_once_with("pro", resolution="1080p")

    @pytest.mark.unit
    def test_video_capabilities_for_tier_merges_sparse_override_onto_delegate_base(self):
        """稀疏用户覆盖（`with_video_capabilities` 的 `overrides` 参数）叠加到 delegate 的档位
        感知基底上,未覆盖字段跟随基底——覆盖必须能翻转执行层看到的能力,但不能整体替换基底,
        否则档位切换时未覆盖字段会冻结在覆盖注入时刻的值。"""
        base_caps = VideoCapabilities(first_frame=True, last_frame=False)
        delegate = AsyncMock()
        delegate.video_capabilities_for_tier = MagicMock(return_value=base_caps)
        backend = CustomVideoBackend(
            provider_id="vid-provider", delegate=delegate, model="kling-v2-5-turbo"
        ).with_video_capabilities(base_caps, overrides={"last_frame": True})

        result = backend.video_capabilities_for_tier("pro")

        assert result.first_frame is True
        assert result.last_frame is True
        assert result.max_reference_images == 0
        delegate.video_capabilities_for_tier.assert_called_once_with("pro", resolution=None)


# ---------------------------------------------------------------------------
# CustomAudioBackend
# ---------------------------------------------------------------------------


class TestCustomAudioBackend:
    @pytest.mark.unit
    def test_properties(self):
        delegate = AsyncMock()
        delegate.capabilities = {AudioCapability.TEXT_TO_SPEECH}
        backend = CustomAudioBackend(provider_id="custom-9", delegate=delegate, model="tts-1")

        assert backend.name == "custom-9"
        assert backend.model == "tts-1"
        assert backend.capabilities == {AudioCapability.TEXT_TO_SPEECH}

    @pytest.mark.unit
    def test_capabilities_delegated(self):
        delegate = AsyncMock()
        delegate.capabilities = {AudioCapability.TEXT_TO_SPEECH}
        backend = CustomAudioBackend(provider_id="audio-provider", delegate=delegate, model="speech-1.5")

        assert backend.capabilities is delegate.capabilities

    @pytest.mark.unit
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

    @pytest.mark.unit
    def test_list_voices_delegates(self):
        from lib.audio_backends.base import VoiceOption

        expected_voices = [VoiceOption(id="alloy", label="alloy")]
        delegate = AsyncMock()
        delegate.list_voices = MagicMock(return_value=expected_voices)
        backend = CustomAudioBackend(provider_id="custom-9", delegate=delegate, model="tts-1")

        assert backend.list_voices() is expected_voices
        delegate.list_voices.assert_called_once_with()

    @pytest.mark.unit
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
