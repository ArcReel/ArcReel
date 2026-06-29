"""AgnesTextBackend 单元测试（OpenAI 兼容 chat/completions，mock SDK 客户端）。

AgnesTextBackend 复用 OpenAITextBackend 的原生 + Instructor 降级流水线，仅替换鉴权
（agnes_shared Bearer 单 key + base_url 归一化）与默认模型 / provider 计费归因。这里覆盖
鉴权 / 归一化 / 能力声明 / usage 解析 / 原生结构化输出 / 选择性 Instructor 降级。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from lib.providers import PROVIDER_AGNES
from lib.text_backends.base import TextCapability, TextGenerationRequest


def _make_mock_response(content="Hello", input_tokens=10, output_tokens=5):
    """构造 mock ChatCompletion 响应。"""
    usage = MagicMock()
    usage.prompt_tokens = input_tokens
    usage.completion_tokens = output_tokens

    message = MagicMock()
    message.content = content

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "stop"

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


class _PersonSchema(BaseModel):
    name: str
    age: int


class TestConstruction:
    def test_name_and_default_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.agnes import AgnesTextBackend

            backend = AgnesTextBackend(api_key="sk")
            assert backend.name == PROVIDER_AGNES
            assert backend.model == "agnes-2.0-flash"

    def test_custom_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.agnes import AgnesTextBackend

            backend = AgnesTextBackend(api_key="sk", model="agnes-2.0-pro")
            assert backend.model == "agnes-2.0-pro"

    def test_capabilities_text_and_structured_no_vision(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.agnes import AgnesTextBackend

            backend = AgnesTextBackend(api_key="sk")
            assert TextCapability.TEXT_GENERATION in backend.capabilities
            assert TextCapability.STRUCTURED_OUTPUT in backend.capabilities
            # vision 未实测，不声明
            assert TextCapability.VISION not in backend.capabilities

    def test_missing_api_key_raises(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.agnes import AgnesTextBackend

            with pytest.raises(ValueError, match="Agnes API Key"):
                AgnesTextBackend(api_key=None)

    def test_base_url_normalized_to_v1(self):
        # 用户填 host（无 /v1 后缀）→ 归一化为 {host}/v1 后传给 SDK。
        with patch("lib.openai_shared.AsyncOpenAI") as mock_ctor:
            from lib.text_backends.agnes import AgnesTextBackend

            AgnesTextBackend(api_key="sk", base_url="https://apihub.agnes-ai.com")
            assert mock_ctor.call_args.kwargs["base_url"] == "https://apihub.agnes-ai.com/v1"

    def test_default_base_url_when_unset(self):
        with patch("lib.openai_shared.AsyncOpenAI") as mock_ctor:
            from lib.text_backends.agnes import AgnesTextBackend

            AgnesTextBackend(api_key="sk")
            assert mock_ctor.call_args.kwargs["base_url"] == "https://apihub.agnes-ai.com/v1"


class TestGenerate:
    async def test_generate_plain_text_parses_usage(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("Test output", 15, 8))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.agnes import AgnesTextBackend

            backend = AgnesTextBackend(api_key="sk")
            result = await backend.generate(TextGenerationRequest(prompt="Say hello"))

        assert result.text == "Test output"
        assert result.provider == PROVIDER_AGNES
        assert result.model == "agnes-2.0-flash"
        assert result.input_tokens == 15
        assert result.output_tokens == 8

    async def test_non_official_base_url_uses_max_tokens(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("ok"))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.agnes import AgnesTextBackend

            backend = AgnesTextBackend(api_key="sk")
            await backend.generate(TextGenerationRequest(prompt="hi", max_output_tokens=4096))

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        # apihub 网关非官方 OpenAI → 保守沿用 max_tokens
        assert call_kwargs["max_tokens"] == 4096
        assert "max_completion_tokens" not in call_kwargs

    async def test_native_structured_output_sends_json_schema(self):
        schema_response = json.dumps({"name": "Alice", "age": 30})
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(schema_response))

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.text_backends.openai._instructor_fallback") as mock_fallback,
        ):
            from lib.text_backends.agnes import AgnesTextBackend

            backend = AgnesTextBackend(api_key="sk")
            result = await backend.generate(TextGenerationRequest(prompt="Extract info", response_schema=_PersonSchema))

        assert result.text == schema_response
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["response_format"]["type"] == "json_schema"
        # 原生成功，不触发降级
        mock_fallback.assert_not_called()

    async def test_non_json_response_triggers_instructor_fallback(self):
        # 原生返回 200 但内容非 JSON（代理静默忽略 response_format）→ 选择性降级到 Instructor。
        markdown_text = "## 提取结果\n\n- 姓名: 张三"
        instructor_result = _PersonSchema(name="Bob", age=25)
        instructor_completion = MagicMock()
        instructor_completion.usage = MagicMock()
        instructor_completion.usage.prompt_tokens = 50
        instructor_completion.usage.completion_tokens = 20

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(markdown_text, 100, 60))

        mock_patched = AsyncMock()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("instructor.from_openai", return_value=mock_patched),
        ):
            from lib.text_backends.agnes import AgnesTextBackend

            backend = AgnesTextBackend(api_key="sk")
            result = await backend.generate(TextGenerationRequest(prompt="Extract info", response_schema=_PersonSchema))

        assert result.text == instructor_result.model_dump_json()
        assert result.provider == PROVIDER_AGNES
        # 原生 200 调用（100/60）已计费，与 Instructor 调用（50/20）的 token 合并计入
        assert result.input_tokens == 150
        assert result.output_tokens == 80
        mock_client.chat.completions.create.assert_awaited_once()
