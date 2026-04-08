"""GeminiTextBackend tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)
from lib.text_backends.gemini import GeminiTextBackend


@pytest.fixture
def mock_genai():
    with patch("lib.text_backends.gemini.genai") as m:
        yield m


class TestProperties:
    def test_name(self, mock_genai):
        b = GeminiTextBackend(api_key="k")
        assert b.name == "gemini"

    def test_default_model(self, mock_genai):
        b = GeminiTextBackend(api_key="k")
        assert b.model == "gemini-3-flash-preview"

    def test_custom_model(self, mock_genai):
        b = GeminiTextBackend(api_key="k", model="custom")
        assert b.model == "custom"

    def test_capabilities(self, mock_genai):
        b = GeminiTextBackend(api_key="k")
        assert b.capabilities == {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    def test_no_api_key_raises(self, mock_genai):
        with pytest.raises(ValueError, match="API Key"):
            GeminiTextBackend()


class TestGenerate:
    @pytest.fixture
    def backend(self, mock_genai):
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        b = GeminiTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    async def test_plain_text(self, backend):
        mock_resp = SimpleNamespace(
            text="  generated text  ",
            usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5),
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert isinstance(result, TextGenerationResult)
        assert result.text == "generated text"
        assert result.provider == "gemini"
        assert result.input_tokens == 10
        assert result.output_tokens == 5

    async def test_structured_output_passes_schema(self, backend):
        mock_resp = SimpleNamespace(
            text='{"key": "value"}',
            usage_metadata=SimpleNamespace(prompt_token_count=20, candidates_token_count=10),
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        result = await backend.generate(TextGenerationRequest(prompt="gen json", response_schema=schema))

        assert result.text == '{"key": "value"}'
        call_kwargs = backend._test_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        assert config["response_mime_type"] == "application/json"
        assert config["response_json_schema"] == schema

    async def test_structured_output_pydantic_class_uses_response_schema(self, backend):
        """When a Pydantic class is passed, response_schema should be used instead of response_json_schema."""
        from pydantic import BaseModel

        class MyModel(BaseModel):
            name: str

        mock_resp = SimpleNamespace(
            text='{"name": "test"}',
            usage_metadata=SimpleNamespace(prompt_token_count=20, candidates_token_count=10),
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        await backend.generate(TextGenerationRequest(prompt="gen", response_schema=MyModel))

        call_kwargs = backend._test_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        assert config["response_mime_type"] == "application/json"
        assert config["response_schema"] is MyModel
        assert "response_json_schema" not in config

    async def test_system_prompt(self, backend):
        mock_resp = SimpleNamespace(
            text="output",
            usage_metadata=None,
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        result = await backend.generate(TextGenerationRequest(prompt="hello", system_prompt="You are X."))

        assert result.text == "output"
        assert result.input_tokens is None
        call_kwargs = backend._test_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        assert config["system_instruction"] == "You are X."

    async def test_no_usage_metadata(self, backend):
        mock_resp = SimpleNamespace(text="output", usage_metadata=None)
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        result = await backend.generate(TextGenerationRequest(prompt="hi"))
        assert result.input_tokens is None
        assert result.output_tokens is None
