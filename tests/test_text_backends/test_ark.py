"""ArkTextBackend tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lib.text_backends.ark import ArkTextBackend
from lib.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult


@pytest.fixture
def mock_ark():
    mock_client = MagicMock()
    with patch("lib.text_backends.ark.create_ark_client", return_value=mock_client) as mock_create:
        yield mock_create, mock_client


class TestProperties:
    def test_name(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.name == "ark"

    def test_default_model(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.model == "doubao-seed-2-0-lite-260215"

    def test_capabilities(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.capabilities == {
            TextCapability.TEXT_GENERATION,
            TextCapability.VISION,
        }

    def test_no_api_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API Key"):
                ArkTextBackend()


class TestGenerate:
    @pytest.fixture
    def backend(self, mock_ark):
        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    async def test_plain_text(self, backend, sync_to_thread):
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  ark output  "))],
            usage=SimpleNamespace(prompt_tokens=15, completion_tokens=8),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert isinstance(result, TextGenerationResult)
        assert result.text == "ark output"
        assert result.provider == "ark"
        assert result.input_tokens == 15
        assert result.output_tokens == 8


class TestCapabilityAwareStructured:
    """Tests for structured output path selection based on model capabilities."""

    @pytest.fixture
    def backend_no_structured(self, mock_ark):
        """Create a backend whose model does not support native structured_output."""
        _, mock_client = mock_ark
        # Use default model doubao-seed-2-0-lite-260215, which has no structured_output in the registry
        b = ArkTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    @pytest.fixture
    def backend_with_structured(self, mock_ark):
        """Create a backend whose model supports native structured_output (simulated)."""
        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k", model="mock-model-with-structured")
        b._test_client = mock_client
        # Manually add native structured output capability
        b._capabilities.add(TextCapability.STRUCTURED_OUTPUT)
        return b

    async def test_default_model_does_not_support_native_structured(self, backend_no_structured):
        """Default model does not support native structured output."""
        assert TextCapability.STRUCTURED_OUTPUT not in backend_no_structured.capabilities

    async def test_fallback_uses_instructor(self, backend_no_structured, sync_to_thread):
        """When model does not support native structured output, falls back to the Instructor path."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            key: str

        sample = TestModel(key="value")

        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            return_value=(sample.model_dump_json(), 50, 20),
        ) as mock_instructor:
            result = await backend_no_structured.generate(
                TextGenerationRequest(prompt="gen", response_schema=TestModel)
            )

            mock_instructor.assert_called_once()
            assert result.text == '{"key":"value"}'
            assert result.input_tokens == 50
            assert result.output_tokens == 20

    async def test_native_path_when_supported(self, backend_with_structured, sync_to_thread):
        """When model supports native structured output, uses the response_format path."""
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"key": "value"}'))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )
        backend_with_structured._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        result = await backend_with_structured.generate(TextGenerationRequest(prompt="gen", response_schema=schema))

        assert result.text == '{"key": "value"}'
        call_args = backend_with_structured._test_client.chat.completions.create.call_args
        assert "response_format" in call_args.kwargs

    async def test_unknown_model_falls_back_to_instructor(self, mock_ark):
        """Unregistered models conservatively fall back to Instructor."""
        b = ArkTextBackend(api_key="k", model="unknown-model-xyz")
        assert TextCapability.STRUCTURED_OUTPUT not in b.capabilities

    async def test_dict_schema_fallback_uses_json_object(self, backend_no_structured, sync_to_thread):
        """Dict schema falls back to the json_object path."""
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"key": "value"}'))],
            usage=SimpleNamespace(prompt_tokens=30, completion_tokens=15),
        )
        backend_no_structured._openai_client.chat.completions.create = MagicMock(return_value=mock_resp)

        result = await backend_no_structured.generate(
            TextGenerationRequest(prompt="gen", response_schema={"type": "object"})
        )

        assert result.text == '{"key": "value"}'
        call_args = backend_no_structured._openai_client.chat.completions.create.call_args
        assert call_args.kwargs["response_format"] == {"type": "json_object"}

    async def test_native_failure_falls_back(self, backend_with_structured, sync_to_thread):
        """Falls back to json_object after native json_schema path fails at runtime."""
        backend_with_structured._test_client.chat.completions.create = MagicMock(
            side_effect=Exception("schema not supported")
        )
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"a": 1}'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        backend_with_structured._openai_client.chat.completions.create = MagicMock(return_value=mock_resp)

        result = await backend_with_structured.generate(
            TextGenerationRequest(prompt="gen", response_schema={"type": "object"})
        )

        assert result.text == '{"a": 1}'
        # Native path should have been attempted
        backend_with_structured._test_client.chat.completions.create.assert_called_once()
        # Fallback path should have been used
        backend_with_structured._openai_client.chat.completions.create.assert_called_once()
