"""instructor_support 模块测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from instructor import Mode
from instructor.core import IncompleteOutputException, InstructorRetryException, ResponseParsingError
from openai import BadRequestError
from pydantic import BaseModel, ValidationError

from lib.text_backends.base import StructuredOutputExhaustedError, TextOutputTruncatedError
from lib.text_backends.instructor_support import (
    generate_structured_via_instructor,
    generate_structured_via_instructor_async,
    instructor_fallback_async,
    instructor_fallback_sync,
)

pytestmark = pytest.mark.unit


class SampleModel(BaseModel):
    name: str
    age: int


def _completion(content: str) -> SimpleNamespace:
    """构造一个只带文本内容的 completion，供诊断日志断言取原始输出。"""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))])


def _retry_exhausted(inner: Exception, *, content: str = "") -> InstructorRetryException:
    """构造 Instructor 档内重试耗尽异常，failed_attempts 决定失败属 wire 层还是校验类。"""
    from instructor.core.exceptions import FailedAttempt

    return InstructorRetryException(
        "retries exhausted",
        last_completion=_completion(content),
        n_attempts=3,
        total_usage=0,
        failed_attempts=[FailedAttempt(attempt_number=1, exception=inner)],
    )


def _validation_error() -> ValidationError:
    try:
        SampleModel.model_validate({"name": "Alice"})
    except ValidationError as exc:
        return exc
    raise AssertionError("SampleModel 缺 age 字段应当校验失败")


def _no_tool_call_error() -> ResponseParsingError:
    return ResponseParsingError("No tool calls or function call found in response", mode="TOOLS")


def _tools_rejected_error() -> BadRequestError:
    message = "tools is not supported by this endpoint"
    return BadRequestError(
        message=message,
        response=httpx.Response(400, request=httpx.Request("POST", "https://proxy.example/v1/chat/completions")),
        body={"error": {"message": message}},
    )


class TestGenerateStructuredViaInstructor:
    def test_returns_json_and_tokens(self):
        """正确返回 JSON 文本和 token 统计。"""
        mock_client = MagicMock()
        sample = SampleModel(name="Alice", age=30)
        mock_completion = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
        )

        with patch("lib.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion.return_value = (
                sample,
                mock_completion,
            )

            json_text, input_tokens, output_tokens = generate_structured_via_instructor(
                client=mock_client,
                model="doubao-seed-2-0-lite-260215",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
            )

        assert json_text == sample.model_dump_json()
        assert input_tokens == 50
        assert output_tokens == 20

    def test_passes_mode_and_retries(self):
        """正确传递 mode 和 max_retries 参数。"""
        from instructor import Mode

        mock_client = MagicMock()
        sample = SampleModel(name="Bob", age=25)
        mock_completion = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

        with patch("lib.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion.return_value = (
                sample,
                mock_completion,
            )

            generate_structured_via_instructor(
                client=mock_client,
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                mode=Mode.MD_JSON,
                max_retries=3,
            )

            # 验证 from_openai 使用了正确的 mode
            mock_instructor.from_openai.assert_called_once_with(mock_client, mode=Mode.MD_JSON)
            # 验证 create_with_completion 使用了正确的参数
            mock_patched.chat.completions.create_with_completion.assert_called_once_with(
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                max_retries=3,
            )

    def test_handles_none_usage(self):
        """completion.usage 为 None 时返回 None token 统计。"""
        mock_client = MagicMock()
        sample = SampleModel(name="Charlie", age=35)
        mock_completion = SimpleNamespace(usage=None)

        with patch("lib.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion.return_value = (
                sample,
                mock_completion,
            )

            json_text, input_tokens, output_tokens = generate_structured_via_instructor(
                client=mock_client,
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
            )

        assert json_text == sample.model_dump_json()
        assert input_tokens is None
        assert output_tokens is None

    def test_max_tokens_uses_default_param_name(self):
        """默认 token_param 下 max_tokens 值以 max_tokens 为参数名上线。"""
        sample = SampleModel(name="Dave", age=40)
        mock_completion = SimpleNamespace(usage=None)

        with patch("lib.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion.return_value = (sample, mock_completion)

            generate_structured_via_instructor(
                client=MagicMock(),
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                max_tokens=1234,
            )

            call_kwargs = mock_patched.chat.completions.create_with_completion.call_args[1]
            assert call_kwargs["max_tokens"] == 1234
            assert "max_completion_tokens" not in call_kwargs

    def test_explicit_token_param_max_completion_tokens(self):
        """显式 token_param 时以 max_completion_tokens 为参数名上线。"""
        sample = SampleModel(name="Eve", age=45)
        mock_completion = SimpleNamespace(usage=None)

        with patch("lib.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion.return_value = (sample, mock_completion)

            generate_structured_via_instructor(
                client=MagicMock(),
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                max_tokens=1234,
                token_param="max_completion_tokens",
            )

            call_kwargs = mock_patched.chat.completions.create_with_completion.call_args[1]
            assert call_kwargs["max_completion_tokens"] == 1234
            assert "max_tokens" not in call_kwargs

    def test_incomplete_output_maps_to_truncated_error(self):
        """Instructor 的 IncompleteOutputException（max_tokens 截断）归一为 TextOutputTruncatedError。"""
        with patch("lib.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion.side_effect = IncompleteOutputException()

            with pytest.raises(TextOutputTruncatedError) as exc_info:
                generate_structured_via_instructor(
                    client=MagicMock(),
                    model="test-model",
                    messages=[{"role": "user", "content": "test"}],
                    response_model=SampleModel,
                    provider="test-provider",
                )

        assert exc_info.value.provider == "test-provider"
        assert exc_info.value.model == "test-model"
        assert isinstance(exc_info.value.__cause__, IncompleteOutputException)


class TestGenerateStructuredViaInstructorAsync:
    async def test_explicit_token_param_max_completion_tokens(self):
        """异步版显式 token_param 时以 max_completion_tokens 为参数名上线。"""
        sample = SampleModel(name="Frank", age=50)
        mock_completion = SimpleNamespace(usage=None)

        with patch("lib.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion = AsyncMock(return_value=(sample, mock_completion))

            await generate_structured_via_instructor_async(
                client=AsyncMock(),
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                max_tokens=2345,
                token_param="max_completion_tokens",
            )

            call_kwargs = mock_patched.chat.completions.create_with_completion.call_args[1]
            assert call_kwargs["max_completion_tokens"] == 2345
            assert "max_tokens" not in call_kwargs

    async def test_incomplete_output_maps_to_truncated_error(self):
        """异步版 IncompleteOutputException 同样归一为 TextOutputTruncatedError。"""
        with patch("lib.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion = AsyncMock(side_effect=IncompleteOutputException())

            with pytest.raises(TextOutputTruncatedError) as exc_info:
                await generate_structured_via_instructor_async(
                    client=AsyncMock(),
                    model="async-model",
                    messages=[{"role": "user", "content": "test"}],
                    response_model=SampleModel,
                    provider="async-provider",
                )

        assert exc_info.value.provider == "async-provider"
        assert exc_info.value.model == "async-model"


class TestInstructorFallbackSync:
    """instructor_fallback_sync 高层函数测试。"""

    def test_pydantic_schema_uses_instructor(self):
        """Pydantic schema 走 instructor 路径，返回正确的 TextGenerationResult。"""
        sample = SampleModel(name="Alice", age=30)

        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            return_value=(sample.model_dump_json(), 50, 20),
        ):
            result = instructor_fallback_sync(
                client=MagicMock(),
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_schema=SampleModel,
                provider="test-provider",
            )

        assert result.text == sample.model_dump_json()
        assert result.provider == "test-provider"
        assert result.model == "test-model"
        assert result.input_tokens == 50
        assert result.output_tokens == 20

    def test_dict_schema_uses_json_object(self):
        """dict schema 走 json_object 路径。"""
        mock_client = MagicMock()
        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"key": "value"}'))],
            usage=SimpleNamespace(prompt_tokens=30, completion_tokens=15),
        )
        mock_client.chat.completions.create.return_value = mock_response

        result = instructor_fallback_sync(
            client=mock_client,
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
            response_schema={"type": "object"},
            provider="test-provider",
        )

        assert result.text == '{"key": "value"}'
        assert result.provider == "test-provider"
        assert result.input_tokens == 30
        assert result.output_tokens == 15
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["response_format"] == {"type": "json_object"}

    def test_pydantic_branch_forwards_token_param(self):
        """Pydantic 分支把 token_param 转发给 generate_structured_via_instructor。"""
        sample = SampleModel(name="Alice", age=30)

        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            return_value=(sample.model_dump_json(), 50, 20),
        ) as mock_gen:
            instructor_fallback_sync(
                client=MagicMock(),
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_schema=SampleModel,
                provider="test-provider",
                max_tokens=500,
                token_param="max_completion_tokens",
            )

        assert mock_gen.call_args[1]["token_param"] == "max_completion_tokens"
        assert mock_gen.call_args[1]["max_tokens"] == 500

    def test_dict_branch_default_token_param(self):
        """dict 分支默认以 max_tokens 为参数名上线。"""
        mock_client = MagicMock()
        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"key": "value"}'))],
            usage=None,
        )
        mock_client.chat.completions.create.return_value = mock_response

        instructor_fallback_sync(
            client=mock_client,
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
            response_schema={"type": "object"},
            provider="test-provider",
            max_tokens=500,
        )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_tokens"] == 500
        assert "max_completion_tokens" not in call_kwargs

    def test_dict_branch_explicit_token_param(self):
        """dict 分支显式 token_param 时以 max_completion_tokens 为参数名上线。"""
        mock_client = MagicMock()
        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"key": "value"}'))],
            usage=None,
        )
        mock_client.chat.completions.create.return_value = mock_response

        instructor_fallback_sync(
            client=mock_client,
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
            response_schema={"type": "object"},
            provider="test-provider",
            max_tokens=500,
            token_param="max_completion_tokens",
        )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_completion_tokens"] == 500
        assert "max_tokens" not in call_kwargs

    def test_dict_schema_truncation_raises(self):
        """dict schema（response_schema 非空，无 Pydantic 模型）截断同样升级为硬错误。"""
        mock_client = MagicMock()
        mock_response = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="partial"), finish_reason="length"),
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=999),
        )
        mock_client.chat.completions.create.return_value = mock_response

        with pytest.raises(TextOutputTruncatedError) as exc_info:
            instructor_fallback_sync(
                client=mock_client,
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_schema={"type": "object"},
                provider="test-provider",
            )

        assert exc_info.value.provider == "test-provider"
        assert exc_info.value.model == "test-model"


class TestStructuredModeChainSync:
    """TOOLS → MD_JSON 降级链（同步版）。"""

    @staticmethod
    def _call(mock_gen):
        return instructor_fallback_sync(
            client=MagicMock(),
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
            response_schema=SampleModel,
            provider="test-provider",
        )

    @staticmethod
    def _modes(mock_gen) -> list[Mode]:
        return [call.kwargs["mode"] for call in mock_gen.call_args_list]

    def test_chain_starts_with_tools_mode(self):
        """首档是 TOOLS：成功即返回，不触碰约束更弱的 MD_JSON。"""
        sample = SampleModel(name="Alice", age=30)
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            return_value=(sample.model_dump_json(), 50, 20),
        ) as mock_gen:
            result = self._call(mock_gen)

        assert self._modes(mock_gen) == [Mode.TOOLS]
        assert result.text == sample.model_dump_json()

    def test_tools_param_rejected_falls_back_to_md_json(self):
        """上游拒收 tools 参数（wire 层）→ 降档到 MD_JSON 并产出合规结果。"""
        sample = SampleModel(name="Bob", age=25)
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[_tools_rejected_error(), (sample.model_dump_json(), 10, 5)],
        ) as mock_gen:
            result = self._call(mock_gen)

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]
        assert result.text == sample.model_dump_json()

    def test_no_tool_call_in_response_falls_back_to_md_json(self):
        """上游收下 tools 却不回 tool call（wire 层）→ 降档到 MD_JSON。"""
        sample = SampleModel(name="Carol", age=28)
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[_retry_exhausted(_no_tool_call_error()), (sample.model_dump_json(), 10, 5)],
        ) as mock_gen:
            result = self._call(mock_gen)

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]
        assert result.text == sample.model_dump_json()

    def test_tools_validation_exhaustion_is_terminal(self):
        """TOOLS 档校验类耗尽不降档：上游确实回了 tool call，换更弱的档只会更差。"""
        exhausted = _retry_exhausted(_validation_error(), content='{"name": "Alice"}')
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[exhausted],
        ) as mock_gen:
            with pytest.raises(StructuredOutputExhaustedError) as exc_info:
                self._call(mock_gen)

        assert self._modes(mock_gen) == [Mode.TOOLS]
        assert exc_info.value.__cause__ is exhausted
        assert exc_info.value.provider == "test-provider"

    def test_md_json_exhaustion_raises_structured_output_exhausted(self):
        """末档耗尽同样收敛为终局异常，不把 InstructorRetryException 原文透出去。"""
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[_tools_rejected_error(), _retry_exhausted(_validation_error())],
        ) as mock_gen:
            with pytest.raises(StructuredOutputExhaustedError, match="结构化输出能力不足"):
                self._call(mock_gen)

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]

    def test_transient_error_propagates_unchanged(self):
        """瞬态错误既不降档也不收敛为终局异常，原样冒泡交调用方的重试装饰器判定。"""
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[ConnectionError("503 service unavailable")],
        ) as mock_gen:
            with pytest.raises(ConnectionError):
                self._call(mock_gen)

        assert self._modes(mock_gen) == [Mode.TOOLS]

    def test_truncation_does_not_fall_back(self):
        """截断是硬错误：重发同一份必然再截断的请求没有意义，不降档。"""
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[TextOutputTruncatedError(provider="test-provider", model="test-model")],
        ) as mock_gen:
            with pytest.raises(TextOutputTruncatedError):
                self._call(mock_gen)

        assert self._modes(mock_gen) == [Mode.TOOLS]

    def test_downgrade_logs_raw_model_output(self, caplog):
        """降档触发点以 warning 记录截断后的模型原始输出。"""
        sample = SampleModel(name="Dave", age=33)
        raw = "抱歉，我无法按要求输出 JSON。"
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[
                _retry_exhausted(_no_tool_call_error(), content=raw),
                (sample.model_dump_json(), 10, 5),
            ],
        ) as mock_gen:
            with caplog.at_level("WARNING", logger="lib.text_backends.instructor_support"):
                self._call(mock_gen)

        assert any(raw in record.getMessage() for record in caplog.records)


class TestStructuredModeChainAsync:
    """TOOLS → MD_JSON 降级链（异步版），与同步版同口径。"""

    @staticmethod
    async def _call():
        return await instructor_fallback_async(
            client=AsyncMock(),
            model="async-model",
            messages=[{"role": "user", "content": "test"}],
            response_schema=SampleModel,
            provider="async-provider",
        )

    @staticmethod
    def _modes(mock_gen) -> list[Mode]:
        return [call.kwargs["mode"] for call in mock_gen.call_args_list]

    async def test_wire_failure_falls_back_to_md_json(self):
        sample = SampleModel(name="Eve", age=45)
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor_async",
            side_effect=[_tools_rejected_error(), (sample.model_dump_json(), 10, 5)],
        ) as mock_gen:
            result = await self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]
        assert result.text == sample.model_dump_json()

    async def test_validation_exhaustion_is_terminal(self):
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor_async",
            side_effect=[_retry_exhausted(_validation_error())],
        ) as mock_gen:
            with pytest.raises(StructuredOutputExhaustedError):
                await self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]

    async def test_transient_error_propagates_unchanged(self):
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor_async",
            side_effect=[ConnectionError("503 service unavailable")],
        ):
            with pytest.raises(ConnectionError):
                await self._call()


class TestInstructorFallbackAsync:
    """instructor_fallback_async 高层函数测试。"""

    async def test_pydantic_schema_uses_instructor_async(self):
        """Pydantic schema 走异步 instructor 路径。"""
        sample = SampleModel(name="Bob", age=25)

        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor_async",
            return_value=(sample.model_dump_json(), 40, 18),
        ):
            result = await instructor_fallback_async(
                client=AsyncMock(),
                model="async-model",
                messages=[{"role": "user", "content": "test"}],
                response_schema=SampleModel,
                provider="async-provider",
            )

        assert result.text == sample.model_dump_json()
        assert result.provider == "async-provider"
        assert result.model == "async-model"
        assert result.input_tokens == 40
        assert result.output_tokens == 18

    async def test_dict_schema_uses_json_object_async(self):
        """dict schema 走异步 json_object 路径。"""
        mock_client = AsyncMock()
        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"k": "v"}'))],
            usage=SimpleNamespace(prompt_tokens=25, completion_tokens=12),
        )
        mock_client.chat.completions.create.return_value = mock_response

        result = await instructor_fallback_async(
            client=mock_client,
            model="async-model",
            messages=[{"role": "user", "content": "test"}],
            response_schema={"type": "object"},
            provider="async-provider",
        )

        assert result.text == '{"k": "v"}'
        assert result.provider == "async-provider"
        assert result.input_tokens == 25
        assert result.output_tokens == 12

    async def test_pydantic_branch_forwards_token_param_async(self):
        """异步 Pydantic 分支把 token_param 转发给 generate_structured_via_instructor_async。"""
        sample = SampleModel(name="Bob", age=25)

        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor_async",
            return_value=(sample.model_dump_json(), 40, 18),
        ) as mock_gen:
            await instructor_fallback_async(
                client=AsyncMock(),
                model="async-model",
                messages=[{"role": "user", "content": "test"}],
                response_schema=SampleModel,
                provider="async-provider",
                max_tokens=600,
                token_param="max_completion_tokens",
            )

        assert mock_gen.call_args[1]["token_param"] == "max_completion_tokens"
        assert mock_gen.call_args[1]["max_tokens"] == 600

    async def test_dict_branch_default_token_param_async(self):
        """异步 dict 分支默认以 max_tokens 为参数名上线。"""
        mock_client = AsyncMock()
        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"k": "v"}'))],
            usage=None,
        )
        mock_client.chat.completions.create.return_value = mock_response

        await instructor_fallback_async(
            client=mock_client,
            model="async-model",
            messages=[{"role": "user", "content": "test"}],
            response_schema={"type": "object"},
            provider="async-provider",
            max_tokens=600,
        )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_tokens"] == 600
        assert "max_completion_tokens" not in call_kwargs

    async def test_dict_branch_explicit_token_param_async(self):
        """异步 dict 分支显式 token_param 时以 max_completion_tokens 为参数名上线。"""
        mock_client = AsyncMock()
        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"k": "v"}'))],
            usage=None,
        )
        mock_client.chat.completions.create.return_value = mock_response

        await instructor_fallback_async(
            client=mock_client,
            model="async-model",
            messages=[{"role": "user", "content": "test"}],
            response_schema={"type": "object"},
            provider="async-provider",
            max_tokens=600,
            token_param="max_completion_tokens",
        )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_completion_tokens"] == 600
        assert "max_tokens" not in call_kwargs

    async def test_dict_schema_truncation_raises_async(self):
        """异步 dict schema 截断同样升级为硬错误。"""
        mock_client = AsyncMock()
        mock_response = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="partial"), finish_reason="length"),
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=999),
        )
        mock_client.chat.completions.create.return_value = mock_response

        with pytest.raises(TextOutputTruncatedError) as exc_info:
            await instructor_fallback_async(
                client=mock_client,
                model="async-model",
                messages=[{"role": "user", "content": "test"}],
                response_schema={"type": "object"},
                provider="async-provider",
            )

        assert exc_info.value.provider == "async-provider"
        assert exc_info.value.model == "async-model"
