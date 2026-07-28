"""OpenAITextBackend 单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from openai import BadRequestError
from pydantic import BaseModel

from lib.providers import PROVIDER_OPENAI
from lib.text_backends.base import (
    ImageInput,
    TextCapability,
    TextGenerationRequest,
)


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


class TestOpenAITextBackend:
    def test_name_and_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            assert backend.name == PROVIDER_OPENAI
            assert backend.model == "gpt-5.4-mini"

    def test_custom_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key", model="gpt-5.4")
            assert backend.model == "gpt-5.4"

    def test_capabilities(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            assert TextCapability.TEXT_GENERATION in backend.capabilities
            assert TextCapability.STRUCTURED_OUTPUT in backend.capabilities
            assert TextCapability.VISION in backend.capabilities

    async def test_generate_plain_text(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("Test output", 15, 8))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(prompt="Say hello")
            result = await backend.generate(request)

        assert result.text == "Test output"
        assert result.provider == PROVIDER_OPENAI
        assert result.model == "gpt-5.4-mini"
        assert result.input_tokens == 15
        assert result.output_tokens == 8

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-5.4-mini"
        assert len(call_kwargs["messages"]) == 1
        assert call_kwargs["messages"][0]["role"] == "user"
        assert call_kwargs["messages"][0]["content"] == "Say hello"

    async def test_generate_with_system_prompt(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("Response"))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Do something",
                system_prompt="You are helpful",
            )
            await backend.generate(request)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][0]["content"] == "You are helpful"
        assert call_kwargs["messages"][1]["role"] == "user"

    async def test_generate_with_vision(self, tmp_path):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("I see a cat"))

        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="What is this?",
                images=[ImageInput(path=img_path)],
            )
            result = await backend.generate(request)

        assert result.text == "I see a cat"
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        user_msg = call_kwargs["messages"][-1]
        assert isinstance(user_msg["content"], list)
        types = [part["type"] for part in user_msg["content"]]
        assert "image_url" in types
        assert "text" in types

    async def test_generate_structured_output(self):
        schema_response = json.dumps({"name": "Alice", "age": 30})
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(schema_response))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                },
            )
            result = await backend.generate(request)

        assert result.text == schema_response
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert "response_format" in call_kwargs

    async def test_generate_usage_none_tolerant(self):
        """usage 为 None 时不应崩溃。"""
        response = _make_mock_response("OK")
        response.usage = None

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(prompt="Hi")
            result = await backend.generate(request)

        assert result.text == "OK"
        assert result.input_tokens is None
        assert result.output_tokens is None


def _make_bad_request_error(message: str = "Invalid schema") -> BadRequestError:
    """构造 OpenAI BadRequestError。"""
    return BadRequestError(
        message=message,
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")),
        body={"error": {"message": message}},
    )


class _PersonSchema(BaseModel):
    name: str
    age: int


class TestInstructorFallback:
    """Instructor 降级路径测试。"""

    async def test_native_structured_output_success_no_fallback(self):
        """原生 response_format 成功时，不走 Instructor 降级。"""
        schema_response = json.dumps({"name": "Alice", "age": 30})
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(schema_response))

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.text_backends.openai._instructor_fallback") as mock_fallback,
        ):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema=_PersonSchema,
            )
            result = await backend.generate(request)

        assert result.text == schema_response
        mock_fallback.assert_not_called()

    async def test_non_json_response_triggers_instructor_fallback_pydantic(self):
        """原生返回 200 但内容非 JSON（OpenAI 兼容代理静默忽略 response_format），应降级到 Instructor。"""
        markdown_text = "## 小说关键信息提取\n\n- 主角: 张三\n- 题材: 都市悬疑\n开放式续集铺垫"
        instructor_result = _PersonSchema(name="Bob", age=25)
        instructor_completion = MagicMock()
        instructor_completion.usage = MagicMock()
        instructor_completion.usage.prompt_tokens = 50
        instructor_completion.usage.completion_tokens = 20

        mock_client = AsyncMock()
        # 原生调用返回 200 + markdown 文本（无异常）
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(markdown_text, 100, 60))

        mock_patched = AsyncMock()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("instructor.from_openai", return_value=mock_patched),
        ):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema=_PersonSchema,
            )
            result = await backend.generate(request)

        assert result.text == instructor_result.model_dump_json()
        assert result.provider == PROVIDER_OPENAI
        # 原生 200 调用（100/60）已被代理计费，与 Instructor 调用（50/20）的 token 合并计入
        assert result.input_tokens == 150
        assert result.output_tokens == 80
        mock_client.chat.completions.create.assert_awaited_once()

    async def test_schema_violating_json_triggers_instructor_fallback_pydantic(self):
        """原生返回 200 + 合法 JSON 但违反 response_schema（代理接受却不强制 schema），应降级到 Instructor。"""
        # 代理返回合法 JSON，但 age 是中文字符串、违反 Pydantic int 约束
        violating_json = json.dumps({"name": "Alice", "age": "三十"}, ensure_ascii=False)
        instructor_result = _PersonSchema(name="Alice", age=30)
        instructor_completion = MagicMock()
        instructor_completion.usage = MagicMock()
        instructor_completion.usage.prompt_tokens = 80
        instructor_completion.usage.completion_tokens = 30

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(violating_json, 100, 60))

        mock_patched = AsyncMock()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("instructor.from_openai", return_value=mock_patched),
        ):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema=_PersonSchema,
            )
            result = await backend.generate(request)

        # 不再把违例 JSON 直接放行，而是返回经 Instructor 校验后的结果
        assert result.text == instructor_result.model_dump_json()
        # 先原生再降级：原生调用恰发生一次，其计费 token（100/60）并入 Instructor 结果（80/30）
        mock_client.chat.completions.create.assert_awaited_once()
        assert result.input_tokens == 180
        assert result.output_tokens == 90
        mock_patched.chat.completions.create_with_completion.assert_awaited_once()

    async def test_coercible_but_non_strict_json_triggers_instructor_fallback(self):
        """原生返回可强转但类型不严格匹配的 JSON（age 为数字字符串 "30"），严格校验下视为未强制 schema，应降级。"""
        # 宽松校验会把 "30" 强转为 30 而放行；strict=True 与原生 response_format 的 strict 对齐，拒绝并降级
        coercible_json = json.dumps({"name": "Alice", "age": "30"})
        instructor_result = _PersonSchema(name="Alice", age=30)
        instructor_completion = MagicMock()
        instructor_completion.usage = MagicMock()
        instructor_completion.usage.prompt_tokens = 70
        instructor_completion.usage.completion_tokens = 25

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(coercible_json, 100, 60))

        mock_patched = AsyncMock()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("instructor.from_openai", return_value=mock_patched),
        ):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema=_PersonSchema,
            )
            result = await backend.generate(request)

        assert result.text == instructor_result.model_dump_json()
        mock_client.chat.completions.create.assert_awaited_once()
        mock_patched.chat.completions.create_with_completion.assert_awaited_once()
        # 原生计费 token（100/60）并入 Instructor 结果（70/25）
        assert result.input_tokens == 170
        assert result.output_tokens == 85

    async def test_missing_required_field_json_triggers_instructor_fallback(self):
        """原生返回缺必填字段的合法 JSON（如 age 缺失），应降级到 Instructor 而非直接放行。"""
        # 缺 age 必填字段
        violating_json = json.dumps({"name": "Bob"})
        instructor_result = _PersonSchema(name="Bob", age=25)
        instructor_completion = MagicMock()
        instructor_completion.usage = None

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(violating_json, 90, 40))

        mock_patched = AsyncMock()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("instructor.from_openai", return_value=mock_patched),
        ):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema=_PersonSchema,
            )
            result = await backend.generate(request)

        # Instructor usage 为 None，结果 token 即原生 200 调用的计费量（90/40）
        assert result.text == instructor_result.model_dump_json()
        mock_client.chat.completions.create.assert_awaited_once()
        assert result.input_tokens == 90
        assert result.output_tokens == 40
        mock_patched.chat.completions.create_with_completion.assert_awaited_once()

    async def test_schema_violating_json_with_dict_schema_no_fallback(self):
        """dict schema 无对应 Pydantic 模型，即便 JSON 违反所声明类型也沿用「仅校验合法 JSON」的既有行为，不降级。"""
        # 合法 JSON，但 age 是字符串、违反 dict schema 声明的 integer——dict schema 路径不做此校验
        violating_json = json.dumps({"name": "Alice", "age": "thirty"})
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(violating_json))

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.text_backends.openai._instructor_fallback") as mock_fallback,
        ):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                },
            )
            result = await backend.generate(request)

        assert result.text == violating_json
        mock_fallback.assert_not_called()

    async def test_bad_request_error_triggers_instructor_fallback_pydantic(self):
        """原生 response_format 抛 BadRequestError 且 schema 为 Pydantic 类时，走 Instructor 降级。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_make_bad_request_error())

        instructor_result = _PersonSchema(name="Bob", age=25)
        instructor_completion = MagicMock()
        instructor_completion.usage = MagicMock()
        instructor_completion.usage.prompt_tokens = 20
        instructor_completion.usage.completion_tokens = 10

        mock_patched = AsyncMock()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("instructor.from_openai", return_value=mock_patched),
        ):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema=_PersonSchema,
            )
            result = await backend.generate(request)

        assert result.text == instructor_result.model_dump_json()
        assert result.provider == PROVIDER_OPENAI
        assert result.input_tokens == 20
        assert result.output_tokens == 10

    async def test_bad_request_error_with_dict_schema_falls_back_to_plain(self):
        """原生 response_format 抛 BadRequestError 且 schema 为 dict 时，降级为无结构化输出的普通调用。"""
        mock_client = AsyncMock()
        # 第一次调用（带 response_format）抛错
        # 第二次调用（不带 response_format）返回正常结果
        fallback_json = json.dumps({"name": "Charlie", "age": 35})
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[_make_bad_request_error(), _make_mock_response(fallback_json, 12, 6)]
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                },
            )
            result = await backend.generate(request)

        assert result.text == fallback_json
        assert result.input_tokens == 12
        assert result.output_tokens == 6
        # 验证第二次调用使用 json_object 模式（而非原生 json_schema）
        second_call_kwargs = mock_client.chat.completions.create.call_args_list[1][1]
        assert second_call_kwargs.get("response_format") == {"type": "json_object"}

    async def test_bad_request_error_without_schema_propagates(self):
        """没有 response_schema 时，BadRequestError 应原样抛出，不做降级。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_make_bad_request_error())

        import pytest

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(prompt="Just chat")
            with pytest.raises(BadRequestError):
                await backend.generate(request)

    async def test_is_schema_error_recognizes_bad_request(self):
        """_is_schema_error 正确识别 BadRequestError。"""
        from lib.text_backends.openai import _is_schema_error

        assert _is_schema_error(_make_bad_request_error()) is True
        assert _is_schema_error(ValueError("other")) is False
        assert _is_schema_error(RuntimeError("test")) is False

    async def test_fallback_transient_error_does_not_replay_native_call(self):
        """降级路径瞬态错误只在降级层重试，不重放已成功（已计费）的原生调用。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("非 JSON 散文"))

        import pytest

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch(
                "lib.text_backends.instructor_support.instructor_fallback_async",
                new=AsyncMock(side_effect=ConnectionError("503 service unavailable")),
            ) as mock_instructor,
            patch("lib.retry.asyncio.sleep", new=AsyncMock()),
        ):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(prompt="Extract info", response_schema=_PersonSchema)
            with pytest.raises(ConnectionError):
                await backend.generate(request)

        # 原生 200 调用只发生一次；降级层自身重试 4 次穷尽
        mock_client.chat.completions.create.assert_awaited_once()
        assert mock_instructor.await_count == 4


class TestMaxOutputTokens:
    """官方端点用 max_completion_tokens，兼容端点保守沿用 max_tokens。"""

    async def test_official_plain_passes_max_completion_tokens(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("ok"))
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k")
            await backend.generate(TextGenerationRequest(prompt="hi", max_output_tokens=32000))

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_completion_tokens"] == 32000
        assert "max_tokens" not in call_kwargs

    async def test_official_structured_passes_max_completion_tokens(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(json.dumps({"name": "x"})))
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            class MyModel(BaseModel):
                name: str

            backend = OpenAITextBackend(api_key="k")
            await backend.generate(TextGenerationRequest(prompt="hi", response_schema=MyModel, max_output_tokens=24000))

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_completion_tokens"] == 24000
        assert "max_tokens" not in call_kwargs

    async def test_no_max_tokens_means_key_absent(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("ok"))
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k")
            await backend.generate(TextGenerationRequest(prompt="hi"))

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert "max_tokens" not in call_kwargs
        assert "max_completion_tokens" not in call_kwargs

    async def test_custom_base_url_uses_max_tokens(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("ok"))
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k", base_url="https://vllm.example.com/v1")
            await backend.generate(TextGenerationRequest(prompt="hi", max_output_tokens=32000))

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_tokens"] == 32000
        assert "max_completion_tokens" not in call_kwargs

    async def test_explicit_official_base_url_uses_max_completion_tokens(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("ok"))
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k", base_url="https://api.openai.com/v1")
            await backend.generate(TextGenerationRequest(prompt="hi", max_output_tokens=32000))

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_completion_tokens"] == 32000
        assert "max_tokens" not in call_kwargs

    async def test_official_dict_schema_fallback_uses_max_completion_tokens(self):
        """dict-schema 降级路径（json_object 模式）也按端点选参数。"""
        mock_client = AsyncMock()
        fallback_json = json.dumps({"name": "x"})
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[_make_bad_request_error(), _make_mock_response(fallback_json)]
        )
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k")
            await backend.generate(
                TextGenerationRequest(
                    prompt="hi",
                    response_schema={"type": "object", "properties": {"name": {"type": "string"}}},
                    max_output_tokens=8000,
                )
            )

        second_call_kwargs = mock_client.chat.completions.create.call_args_list[1][1]
        assert second_call_kwargs["max_completion_tokens"] == 8000
        assert "max_tokens" not in second_call_kwargs

    async def test_custom_base_url_dict_schema_fallback_uses_max_tokens(self):
        mock_client = AsyncMock()
        fallback_json = json.dumps({"name": "x"})
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[_make_bad_request_error(), _make_mock_response(fallback_json)]
        )
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k", base_url="https://vllm.example.com/v1")
            await backend.generate(
                TextGenerationRequest(
                    prompt="hi",
                    response_schema={"type": "object", "properties": {"name": {"type": "string"}}},
                    max_output_tokens=8000,
                )
            )

        second_call_kwargs = mock_client.chat.completions.create.call_args_list[1][1]
        assert second_call_kwargs["max_tokens"] == 8000
        assert "max_completion_tokens" not in second_call_kwargs

    async def test_official_instructor_fallback_uses_max_completion_tokens(self):
        """Pydantic instructor 降级路径端到端穿透到 create_with_completion。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_make_bad_request_error())

        instructor_result = _PersonSchema(name="Bob", age=25)
        instructor_completion = MagicMock()
        instructor_completion.usage = None

        mock_patched = AsyncMock()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("instructor.from_openai", return_value=mock_patched),
        ):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k")
            await backend.generate(
                TextGenerationRequest(prompt="hi", response_schema=_PersonSchema, max_output_tokens=7000)
            )

        instructor_kwargs = mock_patched.chat.completions.create_with_completion.call_args[1]
        assert instructor_kwargs["max_completion_tokens"] == 7000
        assert "max_tokens" not in instructor_kwargs


class TestTruncation:
    """结构化输出被截断时抛 TextOutputTruncatedError；自由文本仅告警（见 docs/adr/0044）。"""

    async def test_structured_truncation_raises(self):
        import pytest

        from lib.text_backends.base import TextOutputTruncatedError

        mock_client = AsyncMock()
        response = _make_mock_response(json.dumps({"name": "x"}))
        response.choices[0].finish_reason = "length"
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            class MyModel(BaseModel):
                name: str

            backend = OpenAITextBackend(api_key="k")
            with pytest.raises(TextOutputTruncatedError) as exc_info:
                await backend.generate(TextGenerationRequest(prompt="hi", response_schema=MyModel))

        assert exc_info.value.model == "gpt-5.4-mini"

    async def test_free_text_truncation_only_warns(self, caplog):
        import logging

        response = _make_mock_response("partial")
        response.choices[0].finish_reason = "length"
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k")
            with caplog.at_level(logging.WARNING, logger="lib.text_backends.base"):
                result = await backend.generate(TextGenerationRequest(prompt="hi"))

        assert result.text == "partial"
        assert any("被截断" in r.message for r in caplog.records)
