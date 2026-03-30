"""GrokTextBackend tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lib.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult
from lib.text_backends.grok import GrokTextBackend


@pytest.fixture
def mock_xai():
    mock_sdk = MagicMock()
    mock_sdk.chat.system = MagicMock(side_effect=lambda x: f"system:{x}")
    mock_sdk.chat.user = MagicMock(side_effect=lambda text, *parts: f"user:{text}")
    mock_sdk.chat.image = MagicMock(side_effect=lambda **kw: f"image:{kw}")
    mock_sdk.Client = MagicMock()
    with patch.dict("sys.modules", {"xai_sdk": mock_sdk, "xai_sdk.chat": mock_sdk.chat}):
        yield mock_sdk


class TestProperties:
    def test_name(self, mock_xai):
        b = GrokTextBackend(api_key="k")
        assert b.name == "grok"

    def test_default_model(self, mock_xai):
        b = GrokTextBackend(api_key="k")
        assert b.model == "grok-4-1-fast-reasoning"

    def test_capabilities(self, mock_xai):
        b = GrokTextBackend(api_key="k")
        assert b.capabilities == {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    def test_no_api_key_raises(self, mock_xai):
        with pytest.raises(ValueError, match="XAI_API_KEY"):
            GrokTextBackend()


class TestGenerate:
    @pytest.fixture
    def backend(self, mock_xai):
        mock_client = MagicMock()
        mock_xai.Client.return_value = mock_client
        b = GrokTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    async def test_plain_text(self, backend):
        mock_chat = MagicMock()
        mock_response = SimpleNamespace(content="  grok output  ")
        mock_chat.sample = MagicMock(return_value=mock_response)
        backend._test_client.chat.create.return_value = mock_chat

        with patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert isinstance(result, TextGenerationResult)
        assert result.text == "grok output"
        assert result.provider == "grok"

    async def test_structured_output(self, backend):
        mock_chat = MagicMock()
        mock_response = SimpleNamespace(content='{"name": "test"}')
        mock_parsed = MagicMock()
        mock_parsed.model_dump_json.return_value = '{"name": "test"}'
        mock_chat.parse = MagicMock(return_value=(mock_response, mock_parsed))
        backend._test_client.chat.create.return_value = mock_chat

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        with patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            result = await backend.generate(TextGenerationRequest(prompt="gen", response_schema=schema))

        assert result.text == '{"name": "test"}'
