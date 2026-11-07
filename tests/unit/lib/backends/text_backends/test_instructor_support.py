"""instructor_support module tests."""

import json
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

from lib.backends.text_backends.base import (
    StructuredOutputExhaustedError,
    TextGenerationResult,
    TextOutputTruncatedError,
)
from lib.backends.text_backends.instructor_support import (
    _PARSE_FAILURE_TYPES,
    generate_structured_via_instructor,
    generate_structured_via_instructor_async,
    instructor_fallback_async,
    instructor_fallback_sync,
)
from tests.fakes import instructor_api_call_exhausted
from tests.http_capture import capture_http, only_request, request_json
from tests.unit.lib.backends.text_backends.response_schema_support import (
    leaked_class_docstrings,
    response_schema_params,
)


class SampleModel(BaseModel):
    name: str
    age: int


def _structured(json_text: str, input_tokens: int, output_tokens: int) -> TextGenerationResult:
    """Successful Instructor-tier result: JSON text and this call’s usage."""
    return TextGenerationResult(
        text=json_text, provider="", model="", input_tokens=input_tokens, output_tokens=output_tokens
    )


def _completion(content: str, *, tool_calls=None, function_call=None) -> SimpleNamespace:
    """Build completions for raw-output log assertions and tool-call presence decisions."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls, function_call=function_call)
            )
        ]
    )


def _retry_exhausted(
    inner: Exception,
    *,
    content: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    earlier_attempts: list[Exception] | None = None,
) -> InstructorRetryException:
    """Build exhausted Instructor-tier exceptions, attaching terminal inner causes through __cause__, matching real shapes.

    prompt_tokens/completion_tokens simulate paid HTTP 200 attempts within tiers. earlier_attempts
    simulates accumulated parse/validation failures; Instructor retains failed_attempts, so nonempty
    lists do not prove model-output failures caused tier termination.
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
    """Missing upstream tool calls mean wire incompatibility."""
    return ResponseParsingError(
        "No tool calls or function call found in response",
        mode="TOOLS",
        raw_response=_completion(""),
    )


def _md_json_parse_error() -> ResponseParsingError:
    """MD_JSON parse failures naturally lack tool calls, indistinguishable in response shape from missing TOOLS calls."""
    return ResponseParsingError(
        "Failed to extract JSON from response",
        mode="MD_JSON",
        raw_response=_completion("not json"),
    )


def _function_call_args_missing_error() -> ResponseParsingError:
    """Legacy function_call exists but lacks arguments: tool_calls remains None, yet this is validation failure."""
    function_call = SimpleNamespace(name="SampleModel", arguments=None)
    return ResponseParsingError(
        "Tool call arguments missing in response",
        mode="TOOLS",
        raw_response=_completion("", function_call=function_call),
    )


def _tool_call_args_invalid_error() -> ResponseParsingError:
    """Existing tool calls with unusable arguments are validation failures, not wire incompatibility."""
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
    """Upstream tools rejection reaches fallback only after Instructor wraps API-call errors."""
    return instructor_api_call_exhausted(_bad_request("tools is not supported by this endpoint"))


@contextmanager
def _recorded_instructor(
    result: tuple[Any, Any], *, is_async: bool = False
) -> Generator[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """Instructor entry recorder captures from_openai/create_with_completion arguments.

    Modes/wire parameter names describe actual endpoint calls, so assert recorded arguments,
    never substitute callable objects.
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

    patched = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()), on=lambda hook_name, handler: None)

    def _from_openai(client: Any, **kwargs: Any) -> Any:
        patched_with.append({"client": client, **kwargs})
        return patched

    with patch("lib.backends.text_backends.instructor_support.instructor.from_openai", _from_openai):
        yield patched_with, calls


class TestGenerateStructuredViaInstructor:
    def test_returns_json_and_tokens(self):
        """Return correct JSON text/token counts."""
        mock_client = MagicMock()
        sample = SampleModel(name="Alice", age=30)
        mock_completion = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
        )

        with patch("lib.backends.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion.return_value = (
                sample,
                mock_completion,
            )

            result = generate_structured_via_instructor(
                client=mock_client,
                model="doubao-seed-2-0-lite-260215",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
            )

        assert result.text == sample.model_dump_json()
        assert result.input_tokens == 50
        assert result.output_tokens == 20

    def test_passes_mode_and_retries(self):
        """Pass mode to from_openai, all other arguments unchanged onto the wire."""
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
        # response_model becomes a docstring-free subclass (TestModelFacingSchemaOnWire), preserving fields/validation.
        assert issubclass(calls[0].pop("response_model"), SampleModel)
        assert calls == [
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "test"}],
                "max_retries": 3,
            }
        ]

    def test_handles_none_usage(self):
        """completion.usage None returns None token counts."""
        mock_client = MagicMock()
        sample = SampleModel(name="Charlie", age=35)
        mock_completion = SimpleNamespace(usage=None)

        with patch("lib.backends.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion.return_value = (
                sample,
                mock_completion,
            )

            result = generate_structured_via_instructor(
                client=mock_client,
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
            )

        assert result.text == sample.model_dump_json()
        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_max_tokens_uses_default_param_name(self):
        """Default token_param sends max_tokens under the max_tokens wire name."""
        sample = SampleModel(name="Dave", age=40)
        mock_completion = SimpleNamespace(usage=None)

        with patch("lib.backends.text_backends.instructor_support.instructor") as mock_instructor:
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
        """Explicit token_param sends values under max_completion_tokens."""
        sample = SampleModel(name="Eve", age=45)
        mock_completion = SimpleNamespace(usage=None)

        with patch("lib.backends.text_backends.instructor_support.instructor") as mock_instructor:
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
        """Normalize Instructor IncompleteOutputException/max_tokens truncation to TextOutputTruncatedError."""
        with patch("lib.backends.text_backends.instructor_support.instructor") as mock_instructor:
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
        """Async explicit token_param sends max_completion_tokens."""
        sample = SampleModel(name="Frank", age=50)
        mock_completion = SimpleNamespace(usage=None)

        with patch("lib.backends.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion = AsyncMock(return_value=(sample, mock_completion))

            result = await generate_structured_via_instructor_async(
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
        assert result.input_tokens is None
        assert result.output_tokens is None

    async def test_incomplete_output_maps_to_truncated_error(self):
        """Async IncompleteOutputException also normalizes to TextOutputTruncatedError."""
        with patch("lib.backends.text_backends.instructor_support.instructor") as mock_instructor:
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
    """High-level instructor_fallback_sync tests."""

    def test_pydantic_schema_uses_instructor(self):
        """Pydantic schemas use Instructor and return correct TextGenerationResult."""
        sample = SampleModel(name="Alice", age=30)

        with patch(
            "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
            return_value=_structured(sample.model_dump_json(), 50, 20),
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
        """Dict schemas use json_object."""
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
        """Pydantic token_param reaches the wire as max_completion_tokens."""
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

        assert issubclass(calls[0].pop("response_model"), SampleModel)
        assert calls == [
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "test"}],
                "max_retries": 2,
                "max_completion_tokens": 500,
            }
        ]
        assert patched_with == [{"client": client, "mode": Mode.TOOLS}]

    def test_dict_branch_default_token_param(self):
        """Dict branches default to max_tokens."""
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
        """Explicit dict token_param sends max_completion_tokens."""
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

    def test_dict_schema_strips_leading_think_block(self):
        """json_object matches native handling, excluding leading thinking blocks from results."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='<think>想一想。</think>\n{"key": "value"}'))],
            usage=None,
        )

        result = instructor_fallback_sync(
            client=mock_client,
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
            response_schema={"type": "object"},
            provider="test-provider",
        )

        assert result.text == '{"key": "value"}'

    def test_dict_schema_truncation_raises(self):
        """Dict-schema truncation, nonempty response_schema without Pydantic, also becomes hard failure."""
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
    """Pin Instructor exception shapes consumed by fallback decisions.

    Distinguish API rejection from invalid model output using terminal ``__cause__`` and
    parse/validation exception classes. These private implementation details can change with
    upgrades. Test real Instructor so shape changes fail rather than silently making every
    failure terminal or downgrading transient errors.
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
        """Terminal parse/validation classification separates model failures from API failures; match Instructor’s set.

        `_RETRYABLE_PARSE_ERRORS` is private, and allowed instructor>=1.16.0 upgrades may rename
        or move it. Import failures signal this: rediscover exceptions recorded in failed_attempts
        and triggering reask in Instructor’s retry loop, then update `_PARSE_FAILURE_TYPES`.
        """
        from instructor.v2.core.retry import _RETRYABLE_PARSE_ERRORS

        assert set(_PARSE_FAILURE_TYPES) == set(_RETRYABLE_PARSE_ERRORS)

    def test_failed_attempts_survive_a_later_api_failure(self):
        """After one parse failure then API failure, failed_attempts retains old records; terminal cause lives only in __cause__.

        Treating nonempty failed_attempts as evidence of model-output termination misclassifies this path.
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
        """Control: invalid model outputs populate failed_attempts rather than __cause__."""
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

    def test_absent_tool_call_is_retried_then_terminates_in_parse_error(self):
        """TOOLS without returned calls is retryable parsing failure; reask appends corrective guidance.

        Pin consumed shapes: terminate only after in-tier exhaustion, with tool_calls=None parse
        errors as direct terminal causes; every retry appends correction instead of resending unchanged requests.
        """
        from openai import OpenAI
        from openai.types.chat import ChatCompletionMessage

        content_only = ChatCompletionMessage(role="assistant", content='<think>…</think>{"name": "Bob", "age": 1}')
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=content_only, finish_reason="stop")],
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
                max_retries=2,
            )

        cause = exc_info.value.__cause__
        assert isinstance(cause, ResponseParsingError)
        assert cause.raw_response.choices[0].message.tool_calls is None
        calls = client.chat.completions.create.call_args_list
        assert len(calls) == 3
        # Instructor mutates message lists in place; recorded calls share objects, so inspect final shapes only.
        assert [m["role"] for m in calls[-1].kwargs["messages"][:3]] == ["user", "assistant", "user"]


class TestStructuredModeChainSync:
    """Synchronous TOOLS → MD_JSON fallback chain."""

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
        """TOOLS runs first; success never touches weaker MD_JSON."""
        sample = SampleModel(name="Alice", age=30)
        with patch(
            "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
            return_value=_structured(sample.model_dump_json(), 50, 20),
        ) as mock_gen:
            result = self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]
        assert [call.kwargs["provider"] for call in mock_gen.call_args_list] == ["test-provider"]
        assert result.text == sample.model_dump_json()

    def test_tools_param_rejected_falls_back_to_md_json(self):
        """Upstream wire-level tools rejection downgrades to compliant MD_JSON output."""
        sample = SampleModel(name="Bob", age=25)
        with patch(
            "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[_tools_rejected_error(), _structured(sample.model_dump_json(), 10, 5)],
        ) as mock_gen:
            result = self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]
        assert result.text == sample.model_dump_json()

    def test_no_tool_call_in_response_falls_back_to_md_json(self):
        """Upstream accepts tools but returns no calls: wire incompatibility downgrades to MD_JSON."""
        sample = SampleModel(name="Carol", age=28)
        with patch(
            "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[_retry_exhausted(_no_tool_call_error()), _structured(sample.model_dump_json(), 10, 5)],
        ) as mock_gen:
            result = self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]
        assert result.text == sample.model_dump_json()

    def test_client_type_error_after_tools_parse_failure_propagates(self):
        """Missing TOOLS calls followed by client TypeError terminate with client errors, propagated unchanged without downgrading."""
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[
                    _retry_exhausted(
                        TypeError("unexpected keyword argument"),
                        earlier_attempts=[_no_tool_call_error()],
                    )
                ],
            ) as mock_gen,
            pytest.raises(TypeError),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]

    def test_client_type_error_after_md_json_parse_failure_propagates(self):
        """MD_JSON parse failure followed by client TypeError keeps reask functional and propagates TypeError, not terminal-output classification."""
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[
                    _tools_rejected_error(),
                    _retry_exhausted(
                        TypeError("unexpected keyword argument"),
                        earlier_attempts=[_md_json_parse_error()],
                    ),
                ],
            ) as mock_gen,
            pytest.raises(TypeError),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]

    def test_function_call_with_missing_args_is_terminal(self):
        """Legacy function_call lacking arguments still proves upstream returned calls; terminal validation never downgrades."""
        sample = SampleModel(name="Eve", age=29)
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[
                    _retry_exhausted(_function_call_args_missing_error()),
                    _structured(sample.model_dump_json(), 10, 5),
                ],
            ) as mock_gen,
            pytest.raises(StructuredOutputExhaustedError) as exc_info,
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]
        assert "模型输出仍不合规" in str(exc_info.value)
        assert "ResponseParsingError" in str(exc_info.value)

    def test_tools_validation_exhaustion_is_terminal(self):
        """Exhausted TOOLS validation never downgrades; actual tool calls exist, and weaker tiers only worsen constraints."""
        exhausted = _retry_exhausted(_validation_error(), content='{"name": "Alice"}')
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
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
        """Final-tier exhaustion becomes terminal domain errors, never raw InstructorRetryException text."""
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[_tools_rejected_error(), _retry_exhausted(_validation_error())],
            ) as mock_gen,
            pytest.raises(StructuredOutputExhaustedError, match="结构化输出能力不足") as exc_info,
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]
        assert "最后一次 ValidationError" in exc_info.value.reason

    def test_transient_error_propagates_unchanged(self):
        """Transient errors neither downgrade nor become terminal output errors; propagate for caller retry decorators."""
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[instructor_api_call_exhausted(ConnectionError("503 service unavailable"))],
            ) as mock_gen,
            pytest.raises(ConnectionError),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]

    def test_truncation_does_not_fall_back(self):
        """Truncation is hard failure; repeating requests guaranteed to truncate is pointless, never downgrade."""
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[TextOutputTruncatedError(provider="test-provider", model="test-model")],
            ) as mock_gen,
            pytest.raises(TextOutputTruncatedError),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]

    def test_downgrade_logs_raw_model_output(self, caplog):
        """Downgrade warnings log length-truncated raw model output."""
        sample = SampleModel(name="Dave", age=33)
        raw = "抱歉，我无法按要求输出 JSON。"
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[
                    _retry_exhausted(_no_tool_call_error(), content=raw),
                    _structured(sample.model_dump_json(), 10, 5),
                ],
            ),
            caplog.at_level("WARNING", logger="lib.backends.text_backends.instructor_support"),
        ):
            self._call()

        assert any(raw in record.getMessage() for record in caplog.records)

    def test_transient_error_keeps_its_type_after_unwrapping(self):
        """Unwrap Instructor wrappers before propagating transient errors, preserving types for caller retry decisions."""
        from lib.infra.retry import BASE_RETRYABLE_ERRORS, should_retry

        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[instructor_api_call_exhausted(ConnectionError("connection reset by peer"))],
            ),
            pytest.raises(ConnectionError) as exc_info,
        ):
            self._call()

        # Messages contain no transient status patterns; exception types alone prove retriability.
        assert should_retry(exc_info.value, BASE_RETRYABLE_ERRORS)

    def test_invalid_tool_call_arguments_are_terminal(self):
        """Returned tool calls with invalid arguments are validation failures, never weaker MD_JSON fallback."""
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[_retry_exhausted(_tool_call_args_invalid_error())],
            ) as mock_gen,
            pytest.raises(StructuredOutputExhaustedError),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]

    def test_api_failure_after_earlier_parse_failure_propagates(self):
        """One parse failure followed by transient errors terminates on the latter, propagated rather than misclassified as invalid output."""
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
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
        """Unrelated HTTP 400, e.g. context overflow, propagates unchanged. Models without STRUCTURED_OUTPUT
        enter this chain directly; treating such errors as tools incompatibility would replace real causes."""
        rejection = _bad_request("This model's maximum context length is 8192 tokens")
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[instructor_api_call_exhausted(rejection)],
            ) as mock_gen,
            pytest.raises(BadRequestError),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS]

    def test_tools_rejection_matched_case_insensitively(self):
        """Proxy rejection wording varies in case; normalize before matching or fallback becomes unreachable."""
        sample = SampleModel(name="Grace", age=31)
        rejection = _bad_request("Tools Are Not Supported By This Endpoint")
        with patch(
            "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[instructor_api_call_exhausted(rejection), _structured(sample.model_dump_json(), 10, 5)],
        ) as mock_gen:
            result = self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]
        assert result.text == sample.model_dump_json()

    def test_downgrade_carries_billed_tokens_of_failed_mode(self):
        """Paid TOOLS HTTP 200 attempts before downgrade merge token usage into final results."""
        sample = SampleModel(name="Heidi", age=29)
        with patch(
            "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[
                _retry_exhausted(_no_tool_call_error(), prompt_tokens=70, completion_tokens=30),
                _structured(sample.model_dump_json(), 10, 5),
            ],
        ):
            result = self._call()

        assert result.input_tokens == 80
        assert result.output_tokens == 35

    def test_downgrade_without_usage_keeps_tokens_untracked(self):
        """Untracked usage on both sides stays None, never collapses to literal zero tokens."""
        sample = SampleModel(name="Ivan", age=26)
        with patch(
            "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
            side_effect=[
                _retry_exhausted(_no_tool_call_error()),
                TextGenerationResult(text=sample.model_dump_json(), provider="", model=""),
            ],
        ):
            result = self._call()

        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_last_mode_wire_rejection_is_terminal(self):
        """Final-tier upstream rejection has no weaker fallback; terminate rather than downgrade forever."""
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
                side_effect=[_tools_rejected_error(), _tools_rejected_error()],
            ) as mock_gen,
            pytest.raises(StructuredOutputExhaustedError, match="被上游拒收"),
        ):
            self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]


class TestStructuredModeChainAsync:
    """Async TOOLS → MD_JSON chain, matching synchronous rules."""

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
            "lib.backends.text_backends.instructor_support.generate_structured_via_instructor_async",
            side_effect=[_tools_rejected_error(), _structured(sample.model_dump_json(), 10, 5)],
        ) as mock_gen:
            result = await self._call()

        assert self._modes(mock_gen) == [Mode.TOOLS, Mode.MD_JSON]
        assert [call.kwargs["provider"] for call in mock_gen.call_args_list] == ["async-provider", "async-provider"]
        assert result.text == sample.model_dump_json()

    async def test_validation_exhaustion_is_terminal(self):
        with (
            patch(
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor_async",
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
                "lib.backends.text_backends.instructor_support.generate_structured_via_instructor_async",
                side_effect=[instructor_api_call_exhausted(ConnectionError("503 service unavailable"))],
            ),
            pytest.raises(ConnectionError),
        ):
            await self._call()

    async def test_downgrade_carries_billed_tokens_of_failed_mode(self):
        sample = SampleModel(name="Judy", age=27)
        with patch(
            "lib.backends.text_backends.instructor_support.generate_structured_via_instructor_async",
            side_effect=[
                _retry_exhausted(_no_tool_call_error(), prompt_tokens=70, completion_tokens=30),
                _structured(sample.model_dump_json(), 10, 5),
            ],
        ):
            result = await self._call()

        assert result.input_tokens == 80
        assert result.output_tokens == 35


class TestStructuredModeChainThroughInstructor:
    """Real Instructor drives fallback; stub SDK boundaries only, with upstream returning body text but never tool calls.

    Opening thinking blocks contain draft JSON; MD_JSON extracts the first parseable body JSON,
    so strip thinking first to obtain final answers after ``</think>``.
    """

    @staticmethod
    def _content_only_completion(*, prompt_tokens: int, completion_tokens: int) -> SimpleNamespace:
        from openai.types import CompletionUsage
        from openai.types.chat import ChatCompletionMessage

        message = ChatCompletionMessage(
            role="assistant",
            content='<think>先草拟 {"name": "Draft", "age": 99}，年龄不对，改掉。</think>\n{"name": "Bob", "age": 1}',
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=CompletionUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

    def test_sync_content_only_response_lands_on_md_json(self):
        """Exhausted TOOLS reask downgrades; MD_JSON parses identical body text, merging billing from every request."""
        from openai import OpenAI

        client = OpenAI(api_key="sk-test", base_url="https://proxy.invalid/v1")
        client.chat.completions.create = MagicMock(
            side_effect=[
                self._content_only_completion(prompt_tokens=11, completion_tokens=7),
                self._content_only_completion(prompt_tokens=12, completion_tokens=6),
                self._content_only_completion(prompt_tokens=13, completion_tokens=5),
                self._content_only_completion(prompt_tokens=14, completion_tokens=4),
            ]
        )

        result = instructor_fallback_sync(
            client=client,
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
            response_schema=SampleModel,
            provider="test-provider",
        )

        assert result.text == SampleModel(name="Bob", age=1).model_dump_json()
        assert client.chat.completions.create.call_count == 4
        assert result.input_tokens == 50
        assert result.output_tokens == 22

    async def test_async_content_only_response_lands_on_md_json(self):
        """Async entry matches production OpenAI-compatible structured-fallback behavior."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key="sk-test", base_url="https://proxy.invalid/v1")
        client.chat.completions.create = AsyncMock(
            side_effect=[
                self._content_only_completion(prompt_tokens=11, completion_tokens=7),
                self._content_only_completion(prompt_tokens=12, completion_tokens=6),
                self._content_only_completion(prompt_tokens=13, completion_tokens=5),
                self._content_only_completion(prompt_tokens=14, completion_tokens=4),
            ]
        )

        result = await instructor_fallback_async(
            client=client,
            model="async-model",
            messages=[{"role": "user", "content": "test"}],
            response_schema=SampleModel,
            provider="async-provider",
        )

        assert result.text == SampleModel(name="Bob", age=1).model_dump_json()
        assert client.chat.completions.create.await_count == 4
        assert result.input_tokens == 50
        assert result.output_tokens == 22


class TestModelFacingSchemaOnWire:
    """Real Instructor/OpenAI SDK request bodies omit class docs in TOOLS descriptions/arguments and MD_JSON injected schemas alike.

    respx intercepts first serialized requests and returns 400 to terminate; inspect wire bodies only.
    """

    _ENDPOINT = "https://proxy.invalid/v1/chat/completions"
    _REJECT = httpx.Response(400, json={"error": {"message": "stop", "type": "invalid_request_error"}})

    @staticmethod
    def _wire_json(request: httpx.Request, mode: Mode) -> str:
        body = request_json(request)
        if mode is Mode.TOOLS:
            return json.dumps(body["tools"], ensure_ascii=False)
        system_content = body["messages"][0]["content"]
        assert '"properties"' in system_content  # Schema injection must exist for later assertions to be meaningful.
        return system_content

    @pytest.mark.parametrize("mode", [Mode.TOOLS, Mode.MD_JSON])
    @pytest.mark.parametrize("model", response_schema_params())
    def test_sync_request_has_no_class_docstrings(self, model: type[BaseModel], mode: Mode):
        from openai import OpenAI

        client = OpenAI(api_key="sk-test", base_url="https://proxy.invalid/v1", max_retries=0)
        with capture_http() as router:
            route = router.post(self._ENDPOINT).mock(return_value=self._REJECT)
            with pytest.raises(InstructorRetryException):
                generate_structured_via_instructor(
                    client=client,
                    model="test-model",
                    messages=[{"role": "user", "content": "test"}],
                    response_model=model,
                    mode=mode,
                )

        assert leaked_class_docstrings(model, self._wire_json(only_request(route), mode)) == []

    @pytest.mark.parametrize("mode", [Mode.TOOLS, Mode.MD_JSON])
    @pytest.mark.parametrize("model", response_schema_params())
    async def test_async_request_has_no_class_docstrings(self, model: type[BaseModel], mode: Mode):
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key="sk-test", base_url="https://proxy.invalid/v1", max_retries=0)
        with capture_http() as router:
            route = router.post(self._ENDPOINT).mock(return_value=self._REJECT)
            with pytest.raises(InstructorRetryException):
                await generate_structured_via_instructor_async(
                    client=client,
                    model="test-model",
                    messages=[{"role": "user", "content": "test"}],
                    response_model=model,
                    mode=mode,
                )

        assert leaked_class_docstrings(model, self._wire_json(only_request(route), mode)) == []


class TestInstructorFallbackAsync:
    """High-level instructor_fallback_async tests."""

    async def test_pydantic_schema_uses_instructor_async(self):
        """Pydantic schemas use async Instructor."""
        sample = SampleModel(name="Bob", age=25)

        with patch(
            "lib.backends.text_backends.instructor_support.generate_structured_via_instructor_async",
            return_value=_structured(sample.model_dump_json(), 40, 18),
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
        """Dict schemas use async json_object."""
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
        """Async Pydantic token_param reaches the wire as max_completion_tokens."""
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

        assert issubclass(calls[0].pop("response_model"), SampleModel)
        assert calls == [
            {
                "model": "async-model",
                "messages": [{"role": "user", "content": "test"}],
                "max_retries": 2,
                "max_completion_tokens": 600,
            }
        ]
        assert patched_with == [{"client": client, "mode": Mode.TOOLS}]
        assert result.input_tokens is None
        assert result.output_tokens is None

    async def test_dict_branch_default_token_param_async(self):
        """Async dict branches default to max_tokens."""
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
        """Explicit async dict token_param sends max_completion_tokens."""
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

    async def test_dict_schema_strips_leading_think_block_async(self):
        """Async json_object also strips leading thinking blocks."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='<think>想一想。</think>\n{"k": "v"}'))],
            usage=None,
        )

        result = await instructor_fallback_async(
            client=mock_client,
            model="async-model",
            messages=[{"role": "user", "content": "test"}],
            response_schema={"type": "object"},
            provider="async-provider",
        )

        assert result.text == '{"k": "v"}'

    async def test_dict_schema_truncation_raises_async(self):
        """Async dict-schema truncation also becomes hard failure."""
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
