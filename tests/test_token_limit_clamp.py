"""test_token_limit_clamp.py — verify TextGenerator clamps max_output_tokens."""

import logging
from unittest.mock import AsyncMock, PropertyMock

import pytest

from lib.config.registry import get_model_max_output_tokens
from lib.text_backends.base import TextGenerationRequest, TextGenerationResult
from lib.text_generator import TextGenerator


# ── Registry lookup ──


class TestGetModelMaxOutputTokens:
    def test_known_model(self):
        limit = get_model_max_output_tokens("ark", "doubao-seed-1-8-251228")
        assert limit == 8192

    def test_known_model_high(self):
        limit = get_model_max_output_tokens("gemini-aistudio", "gemini-3-flash-preview")
        assert limit == 65536

    def test_unknown_provider(self):
        assert get_model_max_output_tokens("nonexistent", "foo") is None

    def test_unknown_model(self):
        assert get_model_max_output_tokens("ark", "nonexistent-model") is None

    def test_image_model_has_no_limit(self):
        limit = get_model_max_output_tokens("ark", "doubao-seedream-5-0-lite-260128")
        assert limit is None


# ── TextGenerator clamp ──


def _make_generator(provider: str = "ark", model: str = "doubao-seed-1-8-251228"):
    """Create a TextGenerator with a mock backend."""
    backend = AsyncMock()
    type(backend).name = PropertyMock(return_value=provider)
    type(backend).model = PropertyMock(return_value=model)
    backend.generate.return_value = TextGenerationResult(
        text="ok", provider=provider, model=model, input_tokens=10, output_tokens=100
    )
    tracker = AsyncMock()
    tracker.start_call.return_value = "call-1"
    return TextGenerator(backend, tracker)


class TestTextGeneratorClamp:
    @pytest.mark.asyncio
    async def test_clamp_when_exceeds(self, caplog):
        gen = _make_generator("ark", "doubao-seed-1-8-251228")
        req = TextGenerationRequest(prompt="hello", max_output_tokens=32000)

        with caplog.at_level(logging.WARNING):
            await gen.generate(req, project_name="test")

        # Verify the backend was called with clamped value
        call_args = gen.backend.generate.call_args[0][0]
        assert call_args.max_output_tokens == 8192
        assert "clamp" in caplog.text.lower() or "已自动" in caplog.text

    @pytest.mark.asyncio
    async def test_no_clamp_within_limit(self):
        gen = _make_generator("gemini-aistudio", "gemini-3-flash-preview")
        req = TextGenerationRequest(prompt="hello", max_output_tokens=32000)

        await gen.generate(req, project_name="test")

        call_args = gen.backend.generate.call_args[0][0]
        assert call_args.max_output_tokens == 32000  # unchanged

    @pytest.mark.asyncio
    async def test_no_clamp_when_none(self):
        gen = _make_generator("ark", "doubao-seed-1-8-251228")
        req = TextGenerationRequest(prompt="hello")  # max_output_tokens=None

        await gen.generate(req, project_name="test")

        call_args = gen.backend.generate.call_args[0][0]
        assert call_args.max_output_tokens is None

    @pytest.mark.asyncio
    async def test_no_clamp_unknown_model(self):
        gen = _make_generator("unknown-provider", "unknown-model")
        req = TextGenerationRequest(prompt="hello", max_output_tokens=99999)

        await gen.generate(req, project_name="test")

        call_args = gen.backend.generate.call_args[0][0]
        assert call_args.max_output_tokens == 99999  # unchanged
