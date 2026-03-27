"""ArkTextBackend tests."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lib.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult
from lib.text_backends.ark import ArkTextBackend


@pytest.fixture
def mock_ark():
    with patch("lib.text_backends.ark.Ark", create=True) as MockArk:
        # Also patch the import inside __init__
        with patch.dict("sys.modules", {"volcenginesdkarkruntime": MagicMock(Ark=MockArk)}):
            yield MockArk


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
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    def test_no_api_key_raises(self, mock_ark):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API Key"):
                ArkTextBackend()


class TestGenerate:
    @pytest.fixture
    def backend(self, mock_ark):
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
        b = ArkTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    async def test_plain_text(self, backend):
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  ark output  "))],
            usage=SimpleNamespace(prompt_tokens=15, completion_tokens=8),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        with patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert isinstance(result, TextGenerationResult)
        assert result.text == "ark output"
        assert result.provider == "ark"
        assert result.input_tokens == 15
        assert result.output_tokens == 8

    async def test_structured_output(self, backend):
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"key": "value"}'))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        with patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            result = await backend.generate(
                TextGenerationRequest(prompt="gen json", response_schema=schema)
            )

        assert result.text == '{"key": "value"}'
        # Verify response_format was passed
        call_args = backend._test_client.chat.completions.create.call_args
        assert "response_format" in call_args.kwargs
