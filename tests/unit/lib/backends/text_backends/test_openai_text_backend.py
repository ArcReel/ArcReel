"""OpenAITextBackend unit tests."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from instructor import Mode
from openai import BadRequestError
from openai.types.completion_usage import CompletionUsage, PromptTokensDetails
from pydantic import BaseModel

from lib.backends.providers import PROVIDER_OPENAI
from lib.backends.text_backends.base import (
    ImageInput,
    TextCapability,
    TextGenerationRequest,
)
from tests.fakes import (
    bounded_poll_clock,
    captured_openai_clients,
    instructor_api_call_exhausted,
    patched_instructor_from_openai,
)


def _make_mock_response(content="Hello", input_tokens=10, output_tokens=5, cached_tokens=None):
    """Build mocked ChatCompletion responses."""
    usage = CompletionUsage(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        prompt_tokens_details=PromptTokensDetails(cached_tokens=cached_tokens),
    )

    message = MagicMock()
    message.content = content

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _make_instructor_client() -> AsyncMock:
    """Instructor-patched clients: create_with_completion is async; on hook registration is synchronous."""
    return AsyncMock(on=MagicMock())


class TestOpenAITextBackend:
    def test_name_and_model(self):
        with captured_openai_clients() as created:
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            assert backend.name == PROVIDER_OPENAI
            assert backend.model == "gpt-5.4-mini"

        # Absent base_url fills official endpoints; SDK retries stay disabled, centrally managed by generate().
        assert created == [{"api_key": "test-key", "base_url": "https://api.openai.com/v1", "max_retries": 0}]

    def test_custom_model(self):
        with captured_openai_clients():
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key", model="gpt-5.4")
            assert backend.model == "gpt-5.4"

    def test_capabilities(self):
        with captured_openai_clients():
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            assert TextCapability.TEXT_GENERATION in backend.capabilities
            assert TextCapability.STRUCTURED_OUTPUT in backend.capabilities
            assert TextCapability.VISION in backend.capabilities

    def test_official_openai_unregistered_model_keeps_structured_output_capability(self):
        with captured_openai_clients():
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key", model="qwen-long", provider_name="openai")

        assert TextCapability.STRUCTURED_OUTPUT in backend.capabilities

    def test_dashscope_qwen_long_omits_structured_output_capability(self):
        with captured_openai_clients():
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key", model="qwen-long", provider_name="dashscope")

        assert TextCapability.STRUCTURED_OUTPUT not in backend.capabilities

    def test_dashscope_structured_model_keeps_structured_output_capability(self):
        with captured_openai_clients():
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key", model="qwen-plus", provider_name="dashscope")

        assert TextCapability.STRUCTURED_OUTPUT in backend.capabilities

    async def test_generate_plain_text(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("Test output", 15, 8))

        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(prompt="Say hello")
            result = await backend.generate(request)

        assert result.text == "Test output"
        assert result.provider == PROVIDER_OPENAI
        assert result.model == "gpt-5.4-mini"
        assert result.input_tokens == 15
        assert result.output_tokens == 8
        assert result.cache_read_tokens is None

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-5.4-mini"
        assert len(call_kwargs["messages"]) == 1
        assert call_kwargs["messages"][0]["role"] == "user"
        assert call_kwargs["messages"][0]["content"] == "Say hello"

    async def test_cache_hits_are_reported_out_of_total_input(self):
        """prompt_tokens is total input; report cache-hit portions separately as cached_tokens."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_mock_response("ok", input_tokens=1000, output_tokens=8, cached_tokens=600)
        )

        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

            result = await OpenAITextBackend(api_key="test-key").generate(TextGenerationRequest(prompt="hi"))

        assert (result.input_tokens, result.cache_read_tokens, result.output_tokens) == (1000, 600, 8)

    async def test_generate_with_system_prompt(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("Response"))

        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

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
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
        img_path.write_bytes(png_bytes)

        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="What is this?",
                images=[ImageInput(path=img_path)],
            )
            result = await backend.generate(request)

        assert result.text == "I see a cat"
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        # Inline local images as data-URI image_url parts before text parts.
        data_uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
        assert call_kwargs["messages"] == [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": "What is this?"},
                ],
            }
        ]

    async def test_generate_structured_output(self):
        schema_response = json.dumps({"name": "Alice", "age": 30})
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(schema_response))

        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

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
        """Missing usage never crashes."""
        response = _make_mock_response("OK")
        response.usage = None

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(prompt="Hi")
            result = await backend.generate(request)

        assert result.text == "OK"
        assert result.input_tokens is None
        assert result.output_tokens is None


def _make_bad_request_error(message: str = "Invalid schema") -> BadRequestError:
    """Construct OpenAI BadRequestError."""
    return BadRequestError(
        message=message,
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")),
        body={"error": {"message": message}},
    )


class _PersonSchema(BaseModel):
    name: str
    age: int


class TestInstructorFallback:
    """Instructor fallback tests."""

    async def test_dashscope_qwen_long_skips_native_structured_output(self):
        """DashScope models without declared native structured_output go directly to Instructor."""
        instructor_result = _PersonSchema(name="Bob", age=25)
        instructor_completion = MagicMock()
        instructor_completion.usage = MagicMock()
        instructor_completion.usage.prompt_tokens = 50
        instructor_completion.usage.completion_tokens = 20

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_mock_response(json.dumps({"name": "Alice", "age": 30}), 100, 60)
        )
        mock_patched = _make_instructor_client()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai(return_value=mock_patched),
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key", model="qwen-long", provider_name="dashscope")
            result = await backend.generate(TextGenerationRequest(prompt="Extract info", response_schema=_PersonSchema))

        assert result.text == instructor_result.model_dump_json()
        mock_client.chat.completions.create.assert_not_awaited()
        mock_patched.chat.completions.create_with_completion.assert_awaited_once()

    async def test_native_structured_output_success_no_fallback(self):
        """Successful native response_format skips Instructor fallback."""
        schema_response = json.dumps({"name": "Alice", "age": 30})
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(schema_response))

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai() as from_openai,
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema=_PersonSchema,
            )
            result = await backend.generate(request)

        assert result.text == schema_response
        # Instructor clients are the sole fallback entry; no construction proves no fallback.
        from_openai.assert_not_called()

    async def test_think_prefixed_json_passes_native_channel(self):
        """Leading <think> blocks before JSON are stripped, letting native responses be accepted."""
        schema_json = json.dumps({"name": "Alice", "age": 30})
        content = f"<think>\n用户要抽取人物信息，年龄是整数。\n</think>\n\n{schema_json}"
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(content, 100, 60))

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai() as from_openai,
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            result = await backend.generate(TextGenerationRequest(prompt="Extract info", response_schema=_PersonSchema))

        assert result.text == schema_json
        assert result.input_tokens == 100
        assert result.output_tokens == 60
        mock_client.chat.completions.create.assert_awaited_once()
        from_openai.assert_not_called()

    async def test_think_block_stripped_from_free_text(self):
        """Free text also returns only bodies after thinking blocks."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_mock_response("<think>先想想。</think>\n\n你好！")
        )

        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            result = await backend.generate(TextGenerationRequest(prompt="Hi"))

        assert result.text == "你好！"

    async def test_think_prefixed_non_json_still_falls_back(self, caplog):
        """Invalid bodies after thinking removal still fall back; logs contain stripped bodies, never thinking text."""
        import logging

        instructor_result = _PersonSchema(name="Bob", age=25)
        instructor_completion = MagicMock()
        instructor_completion.usage = MagicMock()
        instructor_completion.usage.prompt_tokens = 50
        instructor_completion.usage.completion_tokens = 20

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_mock_response("<think>想一想。</think>\n\n主角是张三。", 100, 60)
        )
        mock_patched = _make_instructor_client()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai(return_value=mock_patched),
            caplog.at_level(logging.WARNING, logger="lib.backends.text_backends.openai"),
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            result = await backend.generate(TextGenerationRequest(prompt="Extract info", response_schema=_PersonSchema))

        assert result.text == instructor_result.model_dump_json()
        assert result.input_tokens == 150
        assert result.output_tokens == 80
        fallback_logs = [r.message for r in caplog.records if "降级到带校验的 Instructor 路径" in r.message]
        assert len(fallback_logs) == 1
        assert "主角是张三。" in fallback_logs[0]
        assert "想一想" not in fallback_logs[0]

    async def test_non_json_response_triggers_instructor_fallback_pydantic(self):
        """Native non-JSON HTTP 200 from proxies ignoring response_format falls back to Instructor."""
        markdown_text = "## 小说关键信息提取\n\n- 主角: 张三\n- 题材: 都市悬疑\n开放式续集铺垫"
        instructor_result = _PersonSchema(name="Bob", age=25)
        instructor_completion = MagicMock()
        instructor_completion.usage = MagicMock()
        instructor_completion.usage.prompt_tokens = 50
        instructor_completion.usage.completion_tokens = 20

        mock_client = AsyncMock()
        # Native HTTP 200 returns Markdown without exceptions.
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(markdown_text, 100, 60))

        mock_patched = _make_instructor_client()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai(return_value=mock_patched) as from_openai,
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema=_PersonSchema,
            )
            result = await backend.generate(request)

        assert result.text == instructor_result.model_dump_json()
        assert result.provider == PROVIDER_OPENAI
        # Merge billed native 100/60 tokens with Instructor 50/20.
        assert result.input_tokens == 150
        assert result.output_tokens == 80
        mock_client.chat.completions.create.assert_awaited_once()
        # Reuse clients, start at TOOLS, and forward native model/messages unchanged into Instructor.
        from_openai.assert_called_once_with(mock_client, mode=Mode.TOOLS)
        fallback_kwargs = mock_patched.chat.completions.create_with_completion.call_args.kwargs
        assert fallback_kwargs["model"] == "gpt-5.4-mini"
        assert fallback_kwargs["messages"] == [{"role": "user", "content": "Extract info"}]
        assert issubclass(fallback_kwargs["response_model"], _PersonSchema)

    async def test_schema_violating_json_triggers_instructor_fallback_pydantic(self):
        """Native valid JSON violating response_schema, accepted but unenforced by proxies, falls back to Instructor."""
        # Valid JSON has Chinese-string age violating Pydantic integers.
        violating_json = json.dumps({"name": "Alice", "age": "三十"}, ensure_ascii=False)
        instructor_result = _PersonSchema(name="Alice", age=30)
        instructor_completion = MagicMock()
        instructor_completion.usage = MagicMock()
        instructor_completion.usage.prompt_tokens = 80
        instructor_completion.usage.completion_tokens = 30

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(violating_json, 100, 60))

        mock_patched = _make_instructor_client()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai(return_value=mock_patched),
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema=_PersonSchema,
            )
            result = await backend.generate(request)

        # Return Instructor-validated output instead of admitting invalid JSON.
        assert result.text == instructor_result.model_dump_json()
        # Native runs once before fallback; merge native 100/60 usage into Instructor 80/30.
        mock_client.chat.completions.create.assert_awaited_once()
        assert result.input_tokens == 180
        assert result.output_tokens == 90
        mock_patched.chat.completions.create_with_completion.assert_awaited_once()

    async def test_coercible_but_non_strict_json_triggers_instructor_fallback(self):
        """Native coercible but inexact types, age "30", fail strict validation as unenforced schemas and require fallback."""
        # Nonstrict validation would coerce "30" to 30; strict=True matches native response_format and rejects/downgrades.
        coercible_json = json.dumps({"name": "Alice", "age": "30"})
        instructor_result = _PersonSchema(name="Alice", age=30)
        instructor_completion = MagicMock()
        instructor_completion.usage = MagicMock()
        instructor_completion.usage.prompt_tokens = 70
        instructor_completion.usage.completion_tokens = 25

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(coercible_json, 100, 60))

        mock_patched = _make_instructor_client()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai(return_value=mock_patched),
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema=_PersonSchema,
            )
            result = await backend.generate(request)

        assert result.text == instructor_result.model_dump_json()
        mock_client.chat.completions.create.assert_awaited_once()
        mock_patched.chat.completions.create_with_completion.assert_awaited_once()
        # Merge native 100/60 usage with Instructor 70/25.
        assert result.input_tokens == 170
        assert result.output_tokens == 85

    async def test_missing_required_field_json_triggers_instructor_fallback(self):
        """Native valid JSON missing required fields such as age falls back rather than passes."""
        # Missing required age.
        violating_json = json.dumps({"name": "Bob"})
        instructor_result = _PersonSchema(name="Bob", age=25)
        instructor_completion = MagicMock()
        instructor_completion.usage = None

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(violating_json, 90, 40))

        mock_patched = _make_instructor_client()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai(return_value=mock_patched),
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema=_PersonSchema,
            )
            result = await backend.generate(request)

        # Instructor usage None leaves native billed 90/40 tokens.
        assert result.text == instructor_result.model_dump_json()
        mock_client.chat.completions.create.assert_awaited_once()
        assert result.input_tokens == 90
        assert result.output_tokens == 40
        mock_patched.chat.completions.create_with_completion.assert_awaited_once()

    async def test_schema_violating_json_with_dict_schema_no_fallback(self):
        """Dict schemas lack Pydantic models; keep valid-JSON-only behavior without fallback even for declared type violations."""
        # Valid JSON string age violates declared integer types, but dict paths do not validate them.
        violating_json = json.dumps({"name": "Alice", "age": "thirty"})
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(violating_json))

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai() as from_openai,
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

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
        from_openai.assert_not_called()

    async def test_bad_request_error_triggers_instructor_fallback_pydantic(self):
        """Native response_format BadRequestError with Pydantic schemas falls back to Instructor."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_make_bad_request_error())

        instructor_result = _PersonSchema(name="Bob", age=25)
        instructor_completion = MagicMock()
        instructor_completion.usage = MagicMock()
        instructor_completion.usage.prompt_tokens = 20
        instructor_completion.usage.completion_tokens = 10

        mock_patched = _make_instructor_client()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai(return_value=mock_patched) as from_openai,
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

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
        # Reuse clients, start TOOLS, and forward native model/messages unchanged.
        from_openai.assert_called_once_with(mock_client, mode=Mode.TOOLS)
        fallback_kwargs = mock_patched.chat.completions.create_with_completion.call_args.kwargs
        assert fallback_kwargs["model"] == "gpt-5.4-mini"
        assert fallback_kwargs["messages"] == [{"role": "user", "content": "Extract info"}]
        assert issubclass(fallback_kwargs["response_model"], _PersonSchema)

    async def test_response_format_rejected_succeeds_via_tools_mode(self):
        """Upstream rejects response_format but accepts tools; initial TOOLS fallback yields compliant output."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_make_bad_request_error())

        instructor_result = _PersonSchema(name="Dana", age=31)
        instructor_completion = MagicMock()
        instructor_completion.usage = None
        mock_patched = _make_instructor_client()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai(return_value=mock_patched) as mock_from_openai,
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            result = await backend.generate(TextGenerationRequest(prompt="Extract info", response_schema=_PersonSchema))

        assert result.text == instructor_result.model_dump_json()
        assert [call.kwargs["mode"] for call in mock_from_openai.call_args_list] == [Mode.TOOLS]

    async def test_tools_mode_rejected_falls_back_to_md_json(self):
        """Upstream also rejects tools: downgrade MD_JSON within the same generate invocation."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_make_bad_request_error())

        instructor_result = _PersonSchema(name="Erin", age=27)
        instructor_completion = MagicMock()
        instructor_completion.usage = None

        tools_patched = _make_instructor_client()
        # Instructor wraps upstream tools-rejection API errors before fallback receives them.
        tools_patched.chat.completions.create_with_completion = AsyncMock(
            side_effect=instructor_api_call_exhausted(_make_bad_request_error("tools is not supported"))
        )
        md_json_patched = _make_instructor_client()
        md_json_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai(side_effect=[tools_patched, md_json_patched]) as mock_from_openai,
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            result = await backend.generate(TextGenerationRequest(prompt="Extract info", response_schema=_PersonSchema))

        assert result.text == instructor_result.model_dump_json()
        assert [call.kwargs["mode"] for call in mock_from_openai.call_args_list] == [Mode.TOOLS, Mode.MD_JSON]

    async def test_bad_request_error_with_dict_schema_falls_back_to_plain(self):
        """Native response_format BadRequestError with dict schemas falls back to ordinary calls without structured output."""
        mock_client = AsyncMock()
        # First response_format call raises.
        # Second call without response_format succeeds.
        fallback_json = json.dumps({"name": "Charlie", "age": 35})
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[_make_bad_request_error(), _make_mock_response(fallback_json, 12, 6)]
        )

        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

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
        # Verify second calls use json_object rather than native json_schema.
        second_call_kwargs = mock_client.chat.completions.create.call_args_list[1][1]
        assert second_call_kwargs.get("response_format") == {"type": "json_object"}

    async def test_bad_request_error_without_schema_propagates(self):
        """Without response_schema, propagate BadRequestError unchanged, never downgrade."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_make_bad_request_error())

        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(prompt="Just chat")
            with pytest.raises(BadRequestError):
                await backend.generate(request)

    async def test_is_schema_error_recognizes_bad_request(self):
        """_is_schema_error recognizes BadRequestError."""
        from lib.backends.text_backends.openai import _is_schema_error

        assert _is_schema_error(_make_bad_request_error()) is True
        assert _is_schema_error(ValueError("other")) is False
        assert _is_schema_error(RuntimeError("test")) is False

    async def test_fallback_transient_error_does_not_replay_native_call(self):
        """Transient fallback failures retry within fallback only, never replay successful paid native calls."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("非 JSON 散文"))

        with (
            captured_openai_clients(mock_client),
            patch(
                "lib.backends.text_backends.instructor_support.instructor_fallback_async",
                new=AsyncMock(side_effect=ConnectionError("503 service unavailable")),
            ) as mock_instructor,
            bounded_poll_clock(),
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(prompt="Extract info", response_schema=_PersonSchema)
            with pytest.raises(ConnectionError):
                await backend.generate(request)

        # Native HTTP 200 occurs once; fallback exhausts its own four retries.
        mock_client.chat.completions.create.assert_awaited_once()
        assert mock_instructor.await_count == 4


class TestMaxOutputTokens:
    """Official endpoints use max_completion_tokens; compatible endpoints conservatively retain max_tokens."""

    async def test_official_plain_passes_max_completion_tokens(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("ok"))
        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k")
            await backend.generate(TextGenerationRequest(prompt="hi", max_output_tokens=32000))

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_completion_tokens"] == 32000
        assert "max_tokens" not in call_kwargs

    async def test_official_structured_passes_max_completion_tokens(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response(json.dumps({"name": "x"})))
        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

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
        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k")
            await backend.generate(TextGenerationRequest(prompt="hi"))

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert "max_tokens" not in call_kwargs
        assert "max_completion_tokens" not in call_kwargs

    async def test_custom_base_url_uses_max_tokens(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("ok"))
        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k", base_url="https://vllm.example.com/v1")
            await backend.generate(TextGenerationRequest(prompt="hi", max_output_tokens=32000))

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_tokens"] == 32000
        assert "max_completion_tokens" not in call_kwargs

    async def test_explicit_official_base_url_uses_max_completion_tokens(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_response("ok"))
        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k", base_url="https://api.openai.com/v1")
            await backend.generate(TextGenerationRequest(prompt="hi", max_output_tokens=32000))

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_completion_tokens"] == 32000
        assert "max_tokens" not in call_kwargs

    async def test_official_dict_schema_fallback_uses_max_completion_tokens(self):
        """Dict json_object fallback also chooses token parameters by endpoint."""
        mock_client = AsyncMock()
        fallback_json = json.dumps({"name": "x"})
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[_make_bad_request_error(), _make_mock_response(fallback_json)]
        )
        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

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
        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

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
        """Pydantic Instructor fallback reaches create_with_completion end-to-end."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_make_bad_request_error())

        instructor_result = _PersonSchema(name="Bob", age=25)
        instructor_completion = MagicMock()
        instructor_completion.usage = None

        mock_patched = _make_instructor_client()
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai(return_value=mock_patched),
        ):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k")
            await backend.generate(
                TextGenerationRequest(prompt="hi", response_schema=_PersonSchema, max_output_tokens=7000)
            )

        instructor_kwargs = mock_patched.chat.completions.create_with_completion.call_args[1]
        assert instructor_kwargs["max_completion_tokens"] == 7000
        assert "max_tokens" not in instructor_kwargs


class TestTruncation:
    """Structured truncation raises TextOutputTruncatedError; free text warns/marks results (docs/adr/0044)."""

    async def test_structured_truncation_raises(self):

        from lib.backends.text_backends.base import TextOutputTruncatedError

        mock_client = AsyncMock()
        response = _make_mock_response(json.dumps({"name": "x"}))
        response.choices[0].finish_reason = "length"
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

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

        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="k")
            with caplog.at_level(logging.WARNING, logger="lib.backends.text_backends.base"):
                result = await backend.generate(TextGenerationRequest(prompt="hi"))

        assert result.text == "partial"
        assert result.truncated is True
        assert any("被截断" in r.message for r in caplog.records)
