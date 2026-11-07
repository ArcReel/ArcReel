"""Unit tests for CustomTextBackend / CustomImageBackend / CustomVideoBackend / CustomAudioBackend."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from arcreel_market_core.video_backend_contract import (
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.audio_backends.base import AudioCapability, AudioSynthesisRequest, AudioSynthesisResult
from lib.backends.image_backends.base import ImageCapability, ImageGenerationRequest, ImageGenerationResult
from lib.backends.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult
from lib.billing.metering import Usage
from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)

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
        """The capabilities property comes directly from the delegate."""
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
        """generate() passes the request unchanged to the delegate without modifying it."""
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

    def test_reference_image_limit_delegated(self):
        # Channel limits are the wrapped backend declaration, evaluated with entries for orchestration trimming.
        delegate = AsyncMock()
        delegate.capabilities = {ImageCapability.IMAGE_TO_IMAGE}
        delegate.reference_image_limit = AsyncMock(return_value=16)
        backend = CustomImageBackend(provider_id="img-provider", delegate=delegate, model="gpt-image-2")

        assert asyncio.run(backend.reference_image_limit()) == 16

    async def test_generate_delegates(self, tmp_path: Path):
        output_path = tmp_path / "output.png"
        expected_result = ImageGenerationResult(
            image_path=output_path,
            provider="custom-img",
            model="flux-1",
            usage=Usage(quantities={"output_tokens": 1290}),
        )
        delegate = AsyncMock()
        delegate.generate = AsyncMock(return_value=expected_result)
        delegate.capabilities = {ImageCapability.TEXT_TO_IMAGE}

        backend = CustomImageBackend(provider_id="custom-img", delegate=delegate, model="flux-1")
        request = ImageGenerationRequest(prompt="A mountain landscape", output_path=output_path)
        result = await backend.generate(request)

        # Preserve the channel's own metering; add image count for entries priced per image.
        assert result.image_path == expected_result.image_path
        assert result.usage == Usage(quantities={"output_images": 1, "input_images": 0, "output_tokens": 1290})
        delegate.generate.assert_awaited_once_with(request)

    async def test_channel_without_usage_reports_one_output_and_the_reference_count(self, tmp_path: Path):
        output_path = tmp_path / "output.png"
        delegate = AsyncMock()
        delegate.generate = AsyncMock(
            return_value=ImageGenerationResult(image_path=output_path, provider="custom-img", model="wf")
        )
        delegate.capabilities = {ImageCapability.IMAGE_TO_IMAGE}

        backend = CustomImageBackend(provider_id="custom-img", delegate=delegate, model="wf")
        request = ImageGenerationRequest(
            prompt="p", output_path=output_path, reference_images=[tmp_path / "a.png", tmp_path / "b.png"]
        )
        result = await backend.generate(request)

        assert result.usage == Usage(quantities={"output_images": 1, "input_images": 2})

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
    def test_metered_usage_takes_the_channel_declaration(self, tmp_path: Path):
        declared = Usage(quantities={"video_tokens": 1000})
        delegate = SimpleNamespace(metered_usage=MagicMock(return_value=declared))
        backend = CustomVideoBackend(provider_id="custom-vid", delegate=cast(Any, delegate), model="seedance")
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "v.mp4")

        assert backend.metered_usage("seedance", request) is declared
        delegate.metered_usage.assert_called_once_with("seedance", request, None, entry=None)

    def test_metered_usage_hands_the_delegate_its_catalog_entry(self, tmp_path: Path):
        """When settlement uses ``backend_metered_usage``, the wrapped channel identifies models using the entry supplied by the factory."""
        from lib.backends.video_backends.registry import backend_metered_usage

        entry = cast(Any, object())
        declared = Usage(quantities={"video_tokens": 1000})
        delegate = SimpleNamespace(metered_usage=MagicMock(return_value=declared), catalog_entry=entry)
        backend = CustomVideoBackend(provider_id="custom-vid", delegate=cast(Any, delegate), model="relay-x")
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "v.mp4")

        assert backend.catalog_entry is entry
        assert backend_metered_usage(backend, request) is declared
        delegate.metered_usage.assert_called_once_with("relay-x", request, None, entry=entry)

    def test_metered_usage_falls_back_when_the_channel_does_not_know_the_model(self, tmp_path: Path):
        delegate = SimpleNamespace(metered_usage=MagicMock(side_effect=ValueError("unknown model")))
        backend = CustomVideoBackend(provider_id="custom-vid", delegate=cast(Any, delegate), model="relay-x")
        request = VideoGenerationRequest(
            prompt="p", output_path=tmp_path / "v.mp4", duration_seconds=8, resolution="720p", generate_audio=True
        )

        usage = backend.metered_usage("relay-x", request)

        assert usage.quantities["output_seconds"] == 8
        assert (usage.dimensions["resolution"], usage.dimensions["audio"]) == ("720p", True)

    def test_metered_usage_without_a_declaration_reports_the_sent_duration(self, tmp_path: Path):
        backend = CustomVideoBackend(provider_id="custom-vid", delegate=cast(Any, SimpleNamespace()), model="wf")
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "v.mp4", duration_seconds=5)

        assert backend.metered_usage("wf", request).quantities["output_seconds"] == 5

    def test_properties(self):
        delegate = AsyncMock()
        backend = CustomVideoBackend(provider_id="custom-vid", delegate=delegate, model="wan-pro")

        assert backend.name == "custom-vid"
        assert backend.model == "wan-pro"

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

    async def test_generate_injects_execution_endpoint(self, tmp_path: Path):
        """An instance retaining endpoint injects it into the request before forwarding, so the downstream backend persists it with job_id.

        Use replace rather than in-place modification: the caller owns request, and the wrapper leaves no side effects.
        """
        output_path = tmp_path / "vid.mp4"
        delegate = AsyncMock()
        delegate.generate = AsyncMock(
            return_value=VideoGenerationResult(video_path=output_path, provider="x", model="y", duration_seconds=5)
        )

        backend = CustomVideoBackend(provider_id="p", delegate=delegate, model="m", endpoint="openai-video")
        request = VideoGenerationRequest(prompt="test", output_path=output_path, task_id="T-1")
        await backend.generate(request)

        forwarded = delegate.generate.call_args[0][0]
        assert forwarded.execution_endpoint == "openai-video"
        assert request.execution_endpoint is None

    def test_video_capabilities_for_tier_delegates_when_delegate_supports_it(self):
        """When delegates implement tier-aware queries (such as Kling), forward results for the requested tier instead of falling back
        to context-free video_capabilities; otherwise media_generator getattr finds a shell method that always
        conservatively rejects and provides no effective narrowing."""
        pro_caps = VideoCapabilities(first_frame=True, last_frame=True)
        delegate = AsyncMock()
        delegate.video_capabilities_for_tier = MagicMock(return_value=pro_caps)
        backend = CustomVideoBackend(provider_id="vid-provider", delegate=delegate, model="kling-v2-5-turbo")

        result = backend.video_capabilities_for_tier("pro", resolution="1080p")

        assert result is pro_caps
        delegate.video_capabilities_for_tier.assert_called_once_with("pro", resolution="1080p")

    def test_video_capabilities_for_tier_falls_back_without_tier_support(self):
        """Without delegate tier-aware queries, fall back to context-free video_capabilities."""
        static_caps = VideoCapabilities(first_frame=True, last_frame=False)
        delegate = AsyncMock(spec=["video_capabilities", "capabilities"])
        delegate.video_capabilities = static_caps
        backend = CustomVideoBackend(provider_id="vid-provider", delegate=delegate, model="wan-pro")

        result = backend.video_capabilities_for_tier("pro")

        assert result is static_caps

    def test_video_capabilities_for_tier_ignores_full_injected_capabilities(self):
        """Factory-injected fully composed capabilities (`with_video_capabilities` without overrides, as used by factory.py
        without user overrides) must not short-circuit tier queries: context-free system judgments are conservative
        for tier-sensitive backends such as Kling. Short-circuiting would make production effectively lack tier awareness."""
        static_merged = VideoCapabilities(first_frame=True, last_frame=False)
        pro_caps = VideoCapabilities(first_frame=True, last_frame=True)
        delegate = AsyncMock()
        delegate.video_capabilities_for_tier = MagicMock(return_value=pro_caps)
        backend = CustomVideoBackend(
            provider_id="vid-provider", delegate=delegate, model="kling-v2-5-turbo"
        ).with_video_capabilities(static_merged)

        result = backend.video_capabilities_for_tier("pro", resolution="1080p")

        assert result is pro_caps
        assert backend.name == "vid-provider"
        delegate.video_capabilities_for_tier.assert_called_once_with("pro", resolution="1080p")

    def test_video_capabilities_for_tier_merges_sparse_override_onto_delegate_base(self):
        """Sparse user overrides (`overrides` on `with_video_capabilities`) overlay the delegate's tier-aware base, leaving
        untouched fields following that base. Overrides must change execution-visible capabilities without replacing the whole base,
        which would freeze untouched fields at injection-time values when tiers change."""
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
            text="你好世界",
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

    def test_list_voices_delegates(self):
        from lib.backends.audio_backends.base import VoiceOption

        expected_voices = [VoiceOption(id="alloy", label="alloy")]
        delegate = AsyncMock()
        delegate.list_voices = MagicMock(return_value=expected_voices)
        backend = CustomAudioBackend(provider_id="custom-9", delegate=delegate, model="tts-1")

        assert backend.list_voices() is expected_voices
        delegate.list_voices.assert_called_once_with()

    async def test_synthesize_passes_request_unchanged(self, tmp_path: Path):
        output_path = tmp_path / "seg.wav"
        delegate = AsyncMock()
        delegate.synthesize = AsyncMock(
            return_value=AudioSynthesisResult(provider="x", model="y", text="hi", output_path=output_path)
        )
        delegate.capabilities = set()

        backend = CustomAudioBackend(provider_id="p", delegate=delegate, model="m")
        request = AudioSynthesisRequest(text="hi", output_path=output_path, voice="alloy", speed=1.2)
        await backend.synthesize(request)

        call_args = delegate.synthesize.call_args[0]
        assert call_args[0] is request
