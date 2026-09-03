"""instructor_support 模块测试。"""

from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import instructor
import pytest
from instructor import Mode
from instructor.core import IncompleteOutputException, InstructorRetryException, ResponseParsingError
from openai import BadRequestError
from pydantic import BaseModel, ValidationError

from lib.text_backends.base import StructuredOutputExhaustedError, TextOutputTruncatedError
from lib.text_backends.instructor_support import (
    _PARSE_FAILURE_TYPES,
    generate_structured_via_instructor,
    generate_structured_via_instructor_async,
    instructor_fallback_async,
    instructor_fallback_sync,
)
from tests.fakes import instructor_api_call_exhausted


class SampleModel(BaseModel):
    name: str
    age: int


def _completion(content: str, *, tool_calls=None) -> SimpleNamespace:
    """构造一个 completion，供诊断日志断言取原始输出、供判据看有无 tool call。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls, function_call=None))]
    )


def _retry_exhausted(
    inner: Exception,
    *,
    content: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    earlier_attempts: list[Exception] | None = None,
) -> InstructorRetryException:
    """构造 Instructor 档内重试耗尽异常，终止原因 inner 挂在 __cause__ 上（与真实形态一致）。

    prompt_tokens / completion_tokens 模拟档内那些拿到 HTTP 200、已被计费的尝试。
    earlier_attempts 模拟终止前已累积的解析 / 校验失败——Instructor 不清空 failed_attempts，
    所以它非空并不代表这一档是折在模型输出上。
    """
    from instructor.core.exceptions import FailedAttempt

    recorded = [*(earlier_attempts or []), inner]
    exc = InstructorRetryException(
        "retries exhausted",
        last_completion=_completion(content),
        n_attempts=3,
        total_usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        failed_attempts=[
            FailedAttempt(attempt_number=i, exception=e)
            for i, e in enumerate(recorded, 1)
            if isinstance(e, _PARSE_FAILURE_TYPES)
        ],
    )
    exc.__cause__ = inner
    return exc


def _validation_error() -> ValidationError:
    try:
        SampleModel.model_validate({"name": "Alice"})
    except ValidationError as exc:
        return exc
    raise AssertionError("SampleModel 缺 age 字段应当校验失败")


def _no_tool_call_error() -> ResponseParsingError:
    """上游没回 tool call：wire 层不兼容。"""
    return ResponseParsingError(
        "No tool calls or function call found in response",
        mode="TOOLS",
        raw_response=_completion(""),
    )


def _tool_call_args_invalid_error() -> ResponseParsingError:
    """上游回了 tool call 但 arguments 不可用：属校验类，不是 wire 层不兼容。"""
    tool_call = SimpleNamespace(function=SimpleNamespace(arguments=None))
    return ResponseParsingError(
        "Tool call arguments missing in response",
        mode="TOOLS",
        raw_response=_completion("", tool_calls=[tool_call]),
    )


def _bad_request(message: str) -> BadRequestError:
    return BadRequestError(
        message=message,
        response=httpx.Response(400, request=httpx.Request("POST", "https://proxy.example/v1/chat/completions")),
        body={"error": {"message": message}},
    )


def _tools_rejected_error() -> InstructorRetryException:
    """上游拒收 tools 参数：Instructor 把这次 API 调用异常包起来后才到达降级链。"""
    return instructor_api_call_exhausted(_bad_request("tools is not supported by this endpoint"))


@contextmanager
def _recorded_instructor(
    result: tuple[Any, Any], *, is_async: bool = False
) -> Generator[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """instructor 入口的记录器：记下 from_openai 与 create_with_completion 的参数。

    mode 选择、导线参数名这些契约都是「最终发往端点的调用长什么样」，断言落在记录的参数上，
    而不是替身的调用对象。
    """
    patched_with: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    class _Completions:
        def create_with_completion(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            if is_async:

                async def _await_result() -> tuple[Any, Any]:
                    return result

                return _await_result()
            return result

    patched = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))

    def _from_openai(client: Any, **kwargs: Any) -> Any:
        patched_with.append({"client": client, **kwargs})
        return patched

    with patch("lib.text_backends.instructor_support.instructor.from_openai", _from_openai):
        yield patched_with, calls


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
        """mode 交给 from_openai，其余参数原样上线。"""
        mock_client = MagicMock()
        sample = SampleModel(name="Bob", age=25)
        mock_completion = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

        with _recorded_instructor((sample, mock_completion)) as (patched_with, calls):
            generate_structured_via_instructor(
                client=mock_client,
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                mode=Mode.MD_JSON,
                max_retries=3,
            )

        assert patched_with == [{"client": mock_client, "mode": Mode.MD_JSON}]
        assert calls == [
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "test"}],
                "response_model": SampleModel,
                "max_retries": 3,
            }
        ]

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

            _json_text, input_tokens, output_tokens = await generate_structured_via_instructor_async(
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
        assert input_tokens is None
        assert output_tokens is None

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
        assert result.model == "test-model"
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["response_format"] == {"type": "json_object"}
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["messages"] == [
            {"role": "system", "content": "Respond in JSON format."},
            {"role": "user", "content": "test"},
        ]

    def test_pydantic_branch_forwards_token_param(self):
        """Pydantic 分支的 token_param 一路传到导线：值以 max_completion_tokens 为参数名上线。"""
        sample = SampleModel(name="Alice", age=30)
        completion = SimpleNamespace(usage=None)

        client = MagicMock()
        with _recorded_instructor((sample, completion)) as (patched_with, calls):
            instructor_fallback_sync(
                client=client,
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_schema=SampleModel,
                provider="test-provider",
                max_tokens=500,
                token_param="max_completion_tokens",
            )

        assert calls == [
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "test"}],
                "response_model": SampleModel,
                "max_retries": 2,
                "max_completion_tokens": 500,
            }
        ]
        assert patched_with == [{"client": client, "mode": Mode.TOOLS}]

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
        assert exc_info.value.output_tokens == 999


class TestInstructorExceptionShape:
    """钉住降级链判据所依赖的 Instructor 异常形态。

    判据要区分「API 调用被拒」与「模型输出不合规」，靠的是 Instructor 把终止原因挂在
    ``__cause__`` 上、并按类型区分解析 / 校验类与其余异常。这些都是 Instructor 的实现细节，
    升级依赖时可能变——本组用例对真实 Instructor 校验它们，形态一变即红，避免降级链在无人
    察觉的情况下退化成「任何失败都判终局」或「瞬态错误也降档」。
    """

    def test_api_call_failure_arrives_wrapped(self):
        from openai import OpenAI

        client = OpenAI(api_key="sk-test", base_url="https://proxy.invalid/v1")
        rejection = _bad_request("tools is not supported by this endpoint")
        client.chat.completions.create = MagicMock(side_effect=rejection)
        patched = instructor.from_openai(client, mode=Mode.TOOLS)

        with pytest.raises(InstructorRetryException) as exc_info:
            patched.chat.completions.create_with_completion(
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                max_retries=2,
            )

        assert exc_info.value.failed_attempts == []
        assert exc_info.value.__cause__ is rejection

    def test_parse_failure_types_match_instructors_retryable_set(self):
        """判据靠「终止原因是否属解析 / 校验类」区分模型问题与 API 问题，集合须与 Instructor 一致。

        `_RETRYABLE_PARSE_ERRORS` 是私有符号、不属公开契约，而 pyproject 允许 instructor>=1.14.5，
        升级后它可能改名或搬家。此处导入失败即是该情况：到 instructor 的重试循环里重新找出这批
        「会被记进 failed_attempts 并触发 reask」的异常类型，同步更新 `_PARSE_FAILURE_TYPES`。
        """
        from instructor.v2.core.retry import _RETRYABLE_PARSE_ERRORS

        assert set(_PARSE_FAILURE_TYPES) == set(_RETRYABLE_PARSE_ERRORS)

    def test_failed_attempts_survive_a_later_api_failure(self):
        """解析失败一次后再撞上 API 层错误：failed_attempts 保留旧记录，终止原因只在 __cause__。

        判据若拿 failed_attempts 是否为空当「折在模型输出上」的代理，这条路径会被误判。
        """
        from openai import OpenAI
        from openai.types.chat import ChatCompletionMessage

        bad_json = ChatCompletionMessage(role="assistant", content="不是 JSON")
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=bad_json, finish_reason="stop")],
            usage=None,
        )
        outage = ConnectionError("connection reset by peer")
        client = OpenAI(api_key="sk-test", base_url="https://proxy.invalid/v1")
        client.chat.completions.create = MagicMock(side_effect=[completion, outage])
        patched = instructor.from_openai(client, mode=Mode.MD_JSON)

        with pytest.raises(InstructorRetryException) as exc_info:
            patched.chat.completions.create_with_completion(
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                max_retries=2,
            )

        assert exc_info.value.failed_attempts != []
        assert exc_info.value.__cause__ is outage

    def test_parse_failure_is_recorded_in_failed_attempts(self):
        """对照组：模型输出不合规时失败记进 failed_attempts，而非挂在 __cause__ 上。"""
        from openai import OpenAI

        message = SimpleNamespace(content="不是 JSON", tool_calls=None, refusal=None)
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=None,
        )
        client = OpenAI(api_key="sk-test", base_url="https://proxy.invalid/v1")
        client.chat.completions.create = MagicMock(return_value=completion)
        patched = instructor.from_openai(client, mode=Mode.TOOLS)

        with pytest.raises(InstructorRetryException) as exc_info:
            patched.chat.completions.create_with_completion(
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                max_retries=1,
            )

        assert exc_info.value.failed_attempts != []


class TestStructuredModeChainSync:
    """TOOLS → MD_JSON 降级链（同步版）。"""

    @staticmethod
    def _call():
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
            result = self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]
        assert [call.kwargs["provider"] for call in mock_gen.call_args_list] == ["test-provider"]
        assert result.text == sample.model_dump_json()

    def test_tools_param_rejected_falls_back_to_md_json(self):
        """上游拒收 tools 参数（wire 层）→ 降档到 MD_JSON 并产出合规结果。"""
        sample = SampleModel(name="Bob", age=25)
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[_tools_rejected_error(), (sample.model_dump_json(), 10, 5)],
        ) as mock_gen:
            result = self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]
        assert result.text == sample.model_dump_json()

    def test_no_tool_call_in_response_falls_back_to_md_json(self):
        """上游收下 tools 却不回 tool call（wire 层）→ 降档到 MD_JSON。"""
        sample = SampleModel(name="Carol", age=28)
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[_retry_exhausted(_no_tool_call_error()), (sample.model_dump_json(), 10, 5)],
        ) as mock_gen:
            result = self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]
        assert result.text == sample.model_dump_json()

    def test_tools_validation_exhaustion_is_terminal(self):
        """TOOLS 档校验类耗尽不降档：上游确实回了 tool call，换更弱的档只会更差。"""
        exhausted = _retry_exhausted(_validation_error(), content='{"name": "Alice"}')
        with (
            patch(
                "lib.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[exhausted],
            ) as mock_gen,
            pytest.raises(StructuredOutputExhaustedError) as exc_info,
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]
        assert exc_info.value.__cause__ is exhausted
        assert exc_info.value.provider == "test-provider"
        assert exc_info.value.model == "test-model"

    def test_md_json_exhaustion_raises_structured_output_exhausted(self):
        """末档耗尽同样收敛为终局异常，不把 InstructorRetryException 原文透出去。"""
        with (
            patch(
                "lib.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[_tools_rejected_error(), _retry_exhausted(_validation_error())],
            ) as mock_gen,
            pytest.raises(StructuredOutputExhaustedError, match="结构化输出能力不足") as exc_info,
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]
        assert "最后一次 ValidationError" in exc_info.value.reason

    def test_transient_error_propagates_unchanged(self):
        """瞬态错误既不降档也不收敛为终局异常，原样冒泡交调用方的重试装饰器判定。"""
        with (
            patch(
                "lib.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[instructor_api_call_exhausted(ConnectionError("503 service unavailable"))],
            ) as mock_gen,
            pytest.raises(ConnectionError),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]

    def test_truncation_does_not_fall_back(self):
        """截断是硬错误：重发同一份必然再截断的请求没有意义，不降档。"""
        with (
            patch(
                "lib.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[TextOutputTruncatedError(provider="test-provider", model="test-model")],
            ) as mock_gen,
            pytest.raises(TextOutputTruncatedError),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]

    def test_downgrade_logs_raw_model_output(self, caplog):
        """降档触发点以 warning 记录截断后的模型原始输出。"""
        sample = SampleModel(name="Dave", age=33)
        raw = "抱歉，我无法按要求输出 JSON。"
        with (
            patch(
                "lib.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[
                    _retry_exhausted(_no_tool_call_error(), content=raw),
                    (sample.model_dump_json(), 10, 5),
                ],
            ),
            caplog.at_level("WARNING", logger="lib.text_backends.instructor_support"),
        ):
            self._call()

        assert any(raw in record.getMessage() for record in caplog.records)

    def test_transient_error_keeps_its_type_after_unwrapping(self):
        """瞬态错误剥掉 Instructor 包装后冒泡，保住类型供调用方的重试装饰器判定。"""
        from lib.retry import BASE_RETRYABLE_ERRORS, _should_retry

        with (
            patch(
                "lib.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[instructor_api_call_exhausted(ConnectionError("connection reset by peer"))],
            ),
            pytest.raises(ConnectionError) as exc_info,
        ):
            self._call()

        # 消息文本不含任何瞬态状态码模式，只有异常类型能证明它可重试。
        assert _should_retry(exc_info.value, BASE_RETRYABLE_ERRORS)

    def test_invalid_tool_call_arguments_are_terminal(self):
        """上游回了 tool call 但 arguments 不可用：属校验类，不降到约束更弱的 MD_JSON。"""
        with (
            patch(
                "lib.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[_retry_exhausted(_tool_call_args_invalid_error())],
            ) as mock_gen,
            pytest.raises(StructuredOutputExhaustedError),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]

    def test_api_failure_after_earlier_parse_failure_propagates(self):
        """先解析失败一次、再撞上瞬态错误：终止原因是后者，原样冒泡而非当成模型输出不合规。"""
        with (
            patch(
                "lib.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[
                    _retry_exhausted(
                        ConnectionError("connection reset by peer"),
                        earlier_attempts=[_no_tool_call_error()],
                    )
                ],
            ) as mock_gen,
            pytest.raises(ConnectionError),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]

    def test_non_tools_bad_request_propagates(self):
        """与 tools 无关的 400（如上下文超限）原样冒泡：无 STRUCTURED_OUTPUT 能力位的模型
        不经原生档直接进本链，把这类 400 当 tools 不兼容会替换掉真实的失败原因。"""
        rejection = _bad_request("This model's maximum context length is 8192 tokens")
        with (
            patch(
                "lib.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[instructor_api_call_exhausted(rejection)],
            ) as mock_gen,
            pytest.raises(BadRequestError),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]

    def test_tools_rejection_matched_case_insensitively(self):
        """代理的拒收文案大小写不统一，判据须归一后再匹配，否则降档路径形同虚设。"""
        sample = SampleModel(name="Grace", age=31)
        rejection = _bad_request("Tools Are Not Supported By This Endpoint")
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[instructor_api_call_exhausted(rejection), (sample.model_dump_json(), 10, 5)],
        ) as mock_gen:
            result = self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]
        assert result.text == sample.model_dump_json()

    def test_downgrade_carries_billed_tokens_of_failed_mode(self):
        """TOOLS 档拿到过 HTTP 200（已计费）后才降档，这部分 token 并入最终结果。"""
        sample = SampleModel(name="Heidi", age=29)
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[
                _retry_exhausted(_no_tool_call_error(), prompt_tokens=70, completion_tokens=30),
                (sample.model_dump_json(), 10, 5),
            ],
        ):
            result = self._call()

        assert result.input_tokens == 80
        assert result.output_tokens == 35

    def test_downgrade_without_usage_keeps_tokens_untracked(self):
        """两侧皆未追踪用量时保持 None，不因并账塌成字面 0 token。"""
        sample = SampleModel(name="Ivan", age=26)
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[_retry_exhausted(_no_tool_call_error()), (sample.model_dump_json(), None, None)],
        ):
            result = self._call()

        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_last_mode_wire_rejection_is_terminal(self):
        """末档仍被上游拒收：无更弱的档可退，收敛为终局异常而非无限降档。"""
        with (
            patch(
                "lib.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[_tools_rejected_error(), _tools_rejected_error()],
            ) as mock_gen,
            pytest.raises(StructuredOutputExhaustedError, match="被上游拒收"),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]


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
        assert [call.kwargs["provider"] for call in mock_gen.call_args_list] == ["async-provider", "async-provider"]
        assert result.text == sample.model_dump_json()

    async def test_validation_exhaustion_is_terminal(self):
        with (
            patch(
                "lib.text_backends.instructor_support.generate_structured_via_instructor_async",
                side_effect=[_retry_exhausted(_validation_error())],
            ) as mock_gen,
            pytest.raises(StructuredOutputExhaustedError) as exc_info,
        ):
            await self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]
        assert exc_info.value.provider == "async-provider"
        assert exc_info.value.model == "async-model"

    async def test_transient_error_propagates_unchanged(self):
        with (
            patch(
                "lib.text_backends.instructor_support.generate_structured_via_instructor_async",
                side_effect=[instructor_api_call_exhausted(ConnectionError("503 service unavailable"))],
            ),
            pytest.raises(ConnectionError),
        ):
            await self._call()

    async def test_downgrade_carries_billed_tokens_of_failed_mode(self):
        sample = SampleModel(name="Judy", age=27)
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor_async",
            side_effect=[
                _retry_exhausted(_no_tool_call_error(), prompt_tokens=70, completion_tokens=30),
                (sample.model_dump_json(), 10, 5),
            ],
        ):
            result = await self._call()

        assert result.input_tokens == 80
        assert result.output_tokens == 35


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
        assert result.model == "async-model"
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "async-model"
        assert call_kwargs["messages"] == [
            {"role": "system", "content": "Respond in JSON format."},
            {"role": "user", "content": "test"},
        ]

    async def test_pydantic_branch_forwards_token_param_async(self):
        """异步 Pydantic 分支的 token_param 一路传到导线：值以 max_completion_tokens 为参数名上线。"""
        sample = SampleModel(name="Bob", age=25)
        completion = SimpleNamespace(usage=None)

        client = AsyncMock()
        with _recorded_instructor((sample, completion), is_async=True) as (patched_with, calls):
            result = await instructor_fallback_async(
                client=client,
                model="async-model",
                messages=[{"role": "user", "content": "test"}],
                response_schema=SampleModel,
                provider="async-provider",
                max_tokens=600,
                token_param="max_completion_tokens",
            )

        assert calls == [
            {
                "model": "async-model",
                "messages": [{"role": "user", "content": "test"}],
                "response_model": SampleModel,
                "max_retries": 2,
                "max_completion_tokens": 600,
            }
        ]
        assert patched_with == [{"client": client, "mode": Mode.TOOLS}]
        assert result.input_tokens is None
        assert result.output_tokens is None

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
        assert exc_info.value.output_tokens == 999
