"""MiniMaxTextBackend 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from lib.providers import PROVIDER_MINIMAX
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
)
from lib.text_backends.minimax import DEFAULT_BASE_URL, DEFAULT_MODEL


def _make_mock_response(content="Hello", input_tokens=10, output_tokens=5):
    """构造 mock ChatCompletion 响应。"""
    usage = MagicMock()
    usage.prompt_tokens = input_tokens
    usage.completion_tokens = output_tokens

    message = MagicMock()
    message.content = content

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


class TestMiniMaxTextBackend:
    def test_name_and_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.minimax import MiniMaxTextBackend

            backend = MiniMaxTextBackend(api_key="test-key")
            assert backend.name == PROVIDER_MINIMAX
            assert backend.model == DEFAULT_MODEL

    def test_custom_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.minimax import MiniMaxTextBackend

            backend = MiniMaxTextBackend(api_key="test-key", model="MiniMax-M2.7-highspeed")
            assert backend.model == "MiniMax-M2.7-highspeed"

    def test_default_base_url(self):
        with patch("lib.openai_shared.AsyncOpenAI") as mock_cls:
            from lib.text_backends.minimax import MiniMaxTextBackend

            MiniMaxTextBackend(api_key="test-key")
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["base_url"] == DEFAULT_BASE_URL
            assert DEFAULT_BASE_URL.startswith("https://api.minimax.io")

    def test_custom_base_url(self):
        custom_url = "https://custom.minimax.io/v1"
        with patch("lib.openai_shared.AsyncOpenAI") as mock_cls:
            from lib.text_backends.minimax import MiniMaxTextBackend

            MiniMaxTextBackend(api_key="test-key", base_url=custom_url)
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["base_url"] == custom_url

    def test_capabilities(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.minimax import MiniMaxTextBackend

            backend = MiniMaxTextBackend(api_key="test-key")
            assert TextCapability.TEXT_GENERATION in backend.capabilities
            assert TextCapability.STRUCTURED_OUTPUT in backend.capabilities
            assert TextCapability.VISION in backend.capabilities

    async def test_generate_plain_text(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("Hello world", 15, 8))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.minimax import MiniMaxTextBackend

            backend = MiniMaxTextBackend(api_key="test-key")
            request = TextGenerationRequest(prompt="Say hello")
            result = await backend.generate(request)

        assert result.text == "Hello world"
        assert result.provider == PROVIDER_MINIMAX
        assert result.model == DEFAULT_MODEL
        assert result.input_tokens == 15
        assert result.output_tokens == 8

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == DEFAULT_MODEL
        assert call_kwargs["temperature"] == 1.0
        assert call_kwargs["messages"][-1]["role"] == "user"

    async def test_generate_with_system_prompt(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("OK"))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.minimax import MiniMaxTextBackend

            backend = MiniMaxTextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Do something",
                system_prompt="You are helpful",
            )
            await backend.generate(request)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][0]["content"] == "You are helpful"
        assert call_kwargs["messages"][1]["role"] == "user"

    async def test_temperature_always_set(self):
        """temperature 必须始终为 1.0，不能为 0。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("OK"))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.minimax import MiniMaxTextBackend

            backend = MiniMaxTextBackend(api_key="test-key")
            await backend.generate(TextGenerationRequest(prompt="test"))

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["temperature"] == 1.0
        assert call_kwargs["temperature"] > 0  # MiniMax 不接受 temperature=0

    async def test_generate_usage_none_tolerant(self):
        """usage 为 None 时不应崩溃。"""
        response = _make_mock_response("OK")
        response.usage = None

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.minimax import MiniMaxTextBackend

            backend = MiniMaxTextBackend(api_key="test-key")
            result = await backend.generate(TextGenerationRequest(prompt="Hi"))

        assert result.text == "OK"
        assert result.input_tokens is None
        assert result.output_tokens is None

    async def test_schema_error_falls_back_to_json_object(self):
        """response_format 报错时降级为 json_object 模式。"""
        import httpx
        from openai import BadRequestError

        bad_request = BadRequestError(
            message="schema error",
            response=httpx.Response(400, request=httpx.Request("POST", "https://api.minimax.io/v1/chat/completions")),
            body={"error": {"message": "schema error"}},
        )
        fallback_response = _make_mock_response('{"result": "ok"}')

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[bad_request, fallback_response])

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.minimax import MiniMaxTextBackend

            backend = MiniMaxTextBackend(api_key="test-key")
            result = await backend.generate(
                TextGenerationRequest(
                    prompt="Extract",
                    response_schema={
                        "type": "object",
                        "properties": {"result": {"type": "string"}},
                    },
                )
            )

        assert result.text == '{"result": "ok"}'
        second_call_kwargs = mock_client.chat.completions.create.call_args_list[1][1]
        assert second_call_kwargs["response_format"] == {"type": "json_object"}


class TestMiniMaxRegistry:
    def test_minimax_registered(self):
        """MiniMax 后端已注册到 registry。"""
        from lib.text_backends.registry import get_registered_backends

        assert "minimax" in get_registered_backends()

    def test_minimax_in_provider_registry(self):
        """MiniMax 已在 PROVIDER_REGISTRY 中注册。"""
        from lib.config.registry import PROVIDER_REGISTRY

        assert "minimax" in PROVIDER_REGISTRY
        meta = PROVIDER_REGISTRY["minimax"]
        assert "MiniMax-M2.7" in meta.models
        assert "MiniMax-M2.7-highspeed" in meta.models
        assert meta.models["MiniMax-M2.7"].default is True

    def test_minimax_in_factory_mapping(self):
        """MiniMax 已在 factory 的 PROVIDER_ID_TO_BACKEND 中映射。"""
        from lib.text_backends.factory import PROVIDER_ID_TO_BACKEND

        assert "minimax" in PROVIDER_ID_TO_BACKEND
        assert PROVIDER_ID_TO_BACKEND["minimax"] == "minimax"
