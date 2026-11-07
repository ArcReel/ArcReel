"""ArkTextBackend tests."""

from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lib.backends.text_backends.ark import ArkTextBackend
from lib.backends.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult
from tests.fakes import bounded_poll_clock, captured_ark_clients, patched_instructor_from_openai


@contextmanager
def _recorded_openai_clients() -> Generator[list[dict[str, Any]]]:
    """OpenAI-compatible client-construction recorder, capturing arguments and returning an empty substitute."""
    created: list[dict[str, Any]] = []

    def _create(**kwargs: Any) -> Any:
        created.append(kwargs)
        return MagicMock()

    with patch("lib.backends.text_backends.ark.OpenAI", _create):
        yield created


@contextmanager
def _recorded_instructor_fallback(result: tuple[str, int, int]) -> Generator[list[dict[str, Any]]]:
    """Instructor fallback recorder, capturing arguments and returning fixed JSON/input/output-token results."""
    calls: list[dict[str, Any]] = []
    json_text, input_tokens, output_tokens = result

    def _call(**kwargs: Any) -> TextGenerationResult:
        calls.append(kwargs)
        return TextGenerationResult(
            text=json_text, provider="", model="", input_tokens=input_tokens, output_tokens=output_tokens
        )

    with patch("lib.backends.text_backends.instructor_support.generate_structured_via_instructor", _call):
        yield calls


@contextmanager
def _recorded_instructor_wire(result: tuple[Any, Any]) -> Generator[list[dict[str, Any]]]:
    """Instructor-patched-client recorder, capturing final endpoint arguments."""
    calls: list[dict[str, Any]] = []

    class _Completions:
        def create_with_completion(self, **kwargs: Any) -> tuple[Any, Any]:
            calls.append(kwargs)
            return result

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()), on=lambda hook_name, handler: None)
    with patched_instructor_from_openai(client):
        yield calls


@pytest.fixture
def mock_ark():
    mock_client = MagicMock()
    with patch("lib.backends.text_backends.ark.create_ark_client", return_value=mock_client) as mock_create:
        yield mock_create, mock_client


class TestProperties:
    def test_name(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.name == "ark"

    def test_default_model(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.model == "doubao-seed-2-1-lite-260915"

    def test_capabilities(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.capabilities == {
            TextCapability.TEXT_GENERATION,
            TextCapability.VISION,
            TextCapability.STRUCTURED_OUTPUT,
        }

    def test_no_api_key_raises(self):
        with patch.dict("os.environ", {}, clear=True), pytest.raises(ValueError, match="API Key"):
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
        assert result.model == backend.model
        backend._test_client.chat.completions.create.assert_called_once_with(
            model=backend.model,
            messages=[{"role": "user", "content": "hello"}],
        )


class TestVision:
    async def test_vision_uses_chat_completions(self, mock_ark, sync_to_thread):
        """Vision uses chat.completions.create, sharing plain-response parsing."""
        from lib.backends.text_backends.base import ImageInput

        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k")

        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  style description  "))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        )
        mock_client.chat.completions.create = MagicMock(return_value=mock_resp)

        result = await b.generate(
            TextGenerationRequest(prompt="describe style", images=[ImageInput(url="https://example.com/img.jpg")])
        )

        assert result.text == "style description"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        # Verify chat.completions, not Responses API.
        mock_client.chat.completions.create.assert_called_once()

    async def test_vision_message_format(self, mock_ark, sync_to_thread):
        """Vision builds image_url multimodal messages."""
        from lib.backends.text_backends.base import ImageInput

        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k")

        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        mock_client.chat.completions.create = MagicMock(return_value=mock_resp)

        await b.generate(
            TextGenerationRequest(
                prompt="describe",
                system_prompt="you are helpful",
                images=[ImageInput(url="https://example.com/img.jpg")],
            )
        )

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "you are helpful"}
        assert messages[1]["role"] == "user"
        user_content = messages[1]["content"]
        assert user_content[0] == {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}}
        assert user_content[1] == {"type": "text", "text": "describe"}


class TestCapabilityAwareStructured:
    """Capability-based structured-output routing."""

    @pytest.fixture
    def backend_no_structured(self, mock_ark):
        """Create backends whose models lack native structured_output."""
        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k", model="doubao-seed-2-0-mini-260428")
        b._test_client = mock_client
        return b

    @pytest.fixture
    def backend_with_structured(self, mock_ark):
        """Create simulated backends whose models support native structured_output."""
        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k", model="mock-model-with-structured")
        b._test_client = mock_client
        # Manually add native structured-output capability.
        b._capabilities.add(TextCapability.STRUCTURED_OUTPUT)
        return b

    async def test_model_without_structured_output_lacks_native_capability(self, backend_no_structured):
        """Doubao models without catalog structured_output lack native support."""
        assert TextCapability.STRUCTURED_OUTPUT not in backend_no_structured.capabilities

    async def test_fallback_uses_instructor(self, backend_no_structured, sync_to_thread):
        """Models without native support use Instructor fallback."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            key: str

        sample = TestModel(key="value")

        with patch(
            "lib.backends.text_backends.instructor_support.generate_structured_via_instructor",
            return_value=TextGenerationResult(
                text=sample.model_dump_json(), provider="", model="", input_tokens=50, output_tokens=20
            ),
        ) as mock_instructor:
            result = await backend_no_structured.generate(
                TextGenerationRequest(prompt="gen", response_schema=TestModel)
            )

            mock_instructor.assert_called_once()
            assert result.text == '{"key":"value"}'
            assert result.input_tokens == 50
            assert result.output_tokens == 20

    async def test_native_path_when_supported(self, backend_with_structured, sync_to_thread):
        """Models with native support use response_format."""
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"key": "value"}'))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )
        backend_with_structured._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        result = await backend_with_structured.generate(TextGenerationRequest(prompt="gen", response_schema=schema))

        assert result.text == '{"key": "value"}'
        backend_with_structured._test_client.chat.completions.create.assert_called_once_with(
            model="mock-model-with-structured",
            messages=[{"role": "user", "content": "gen"}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema},
            },
        )

    async def test_unknown_model_falls_back_to_instructor(self, mock_ark):
        """Unregistered models conservatively fall back to Instructor."""
        b = ArkTextBackend(api_key="k", model="unknown-model-xyz")
        assert TextCapability.STRUCTURED_OUTPUT not in b.capabilities

    async def test_dict_schema_fallback_uses_json_object(self, backend_no_structured, sync_to_thread):
        """Dict schemas fall back through json_object."""
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

    async def test_truncation_warning_logged_on_finish_reason_length(
        self, backend_no_structured, sync_to_thread, caplog
    ):
        """Ark finish_reason=length logs WARNING and marks truncated results."""
        import logging

        mock_resp = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="partial"),
                    finish_reason="length",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=8192),
        )
        backend_no_structured._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        with caplog.at_level(logging.WARNING, logger="lib.backends.text_backends.base"):
            result = await backend_no_structured.generate(TextGenerationRequest(prompt="hi"))

        assert result.truncated is True
        assert any("被截断" in r.message for r in caplog.records)

    async def test_native_structured_truncation_raises_and_skips_instructor_fallback(
        self, backend_with_structured, sync_to_thread
    ):
        """Native structured truncation raises TextOutputTruncatedError directly, never retries through Instructor."""
        from lib.backends.text_backends.base import TextOutputTruncatedError

        mock_resp = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="partial"),
                    finish_reason="length",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=8192),
        )
        backend_with_structured._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        with patch("lib.backends.text_backends.instructor_support.instructor_fallback_sync") as mock_fallback:
            with pytest.raises(TextOutputTruncatedError) as exc_info:
                await backend_with_structured.generate(
                    TextGenerationRequest(prompt="gen", response_schema={"type": "object"})
                )
            mock_fallback.assert_not_called()

        assert exc_info.value.provider == "ark"
        assert exc_info.value.model == "mock-model-with-structured"
        assert exc_info.value.output_tokens == 8192

    async def test_max_output_tokens_plain(self, backend_no_structured, sync_to_thread):
        """Plain paths forward max_tokens."""
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="x"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        backend_no_structured._test_client.chat.completions.create = MagicMock(return_value=mock_resp)
        await backend_no_structured.generate(TextGenerationRequest(prompt="hi", max_output_tokens=16000))
        call_kwargs = backend_no_structured._test_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 16000
        assert "max_completion_tokens" not in call_kwargs

    async def test_max_output_tokens_structured_native(self, backend_with_structured, sync_to_thread):
        """Native structured paths forward max_tokens."""
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"a":1}'))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        backend_with_structured._test_client.chat.completions.create = MagicMock(return_value=mock_resp)
        await backend_with_structured.generate(
            TextGenerationRequest(prompt="g", response_schema={"type": "object"}, max_output_tokens=20000)
        )
        call_kwargs = backend_with_structured._test_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 20000
        assert "max_completion_tokens" not in call_kwargs

    async def test_max_output_tokens_instructor_fallback(self, backend_no_structured, sync_to_thread):
        """Instructor fallback forwards max_tokens."""
        from pydantic import BaseModel

        class M(BaseModel):
            k: str

        sample = M(k="v")
        with _recorded_instructor_fallback((sample.model_dump_json(), 1, 1)) as calls:
            await backend_no_structured.generate(
                TextGenerationRequest(prompt="g", response_schema=M, max_output_tokens=24000)
            )
        assert [(c["max_tokens"], c["token_param"]) for c in calls] == [(24000, "max_tokens")]

    async def test_instructor_fallback_wire_param_is_max_tokens(self, backend_no_structured, sync_to_thread):
        """Wire guard: Ark fallback’s final endpoint token parameter must be max_tokens."""
        from pydantic import BaseModel

        class M(BaseModel):
            k: str

        sample = M(k="v")
        completion = SimpleNamespace(usage=None)

        with _recorded_instructor_wire((sample, completion)) as calls:
            await backend_no_structured.generate(
                TextGenerationRequest(prompt="g", response_schema=M, max_output_tokens=24000)
            )

        assert len(calls) == 1
        assert calls[0]["max_tokens"] == 24000
        assert "max_completion_tokens" not in calls[0]

    async def test_native_failure_falls_back(self, backend_with_structured, sync_to_thread):
        """Runtime native json_schema failure falls back to json_object."""
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
        # Native path was attempted.
        backend_with_structured._test_client.chat.completions.create.assert_called_once()
        # Fallback path was used.
        backend_with_structured._openai_client.chat.completions.create.assert_called_once()


class TestSuccessPathReverify:
    """Revalidate native HTTP 200 schemas; violations fall back with validation, merging billed native tokens."""

    @pytest.fixture
    def backend(self, mock_ark):
        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k", model="mock-model-with-structured")
        b._test_client = mock_client
        b._capabilities.add(TextCapability.STRUCTURED_OUTPUT)
        return b

    async def test_violating_json_pydantic_triggers_fallback_with_token_merge(self, backend, sync_to_thread):
        """Native HTTP 200 schema-invalid JSON triggers fallback/token merging."""
        import json

        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        violating = json.dumps({"name": "Alice", "age": "三十"}, ensure_ascii=False)
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=violating))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=60),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        instructor_result = Person(name="Alice", age=30)
        completion = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=80, completion_tokens=30))
        mock_patched = MagicMock()
        mock_patched.chat.completions.create_with_completion = MagicMock(return_value=(instructor_result, completion))

        with patched_instructor_from_openai(mock_patched):
            result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        assert result.text == instructor_result.model_dump_json()
        # Proxy-billed native 100/60 tokens merge with Instructor 80/30.
        assert result.input_tokens == 180
        assert result.output_tokens == 90
        backend._test_client.chat.completions.create.assert_called_once()

    async def test_non_json_triggers_fallback(self, backend, sync_to_thread):
        """Native HTTP 200 non-JSON, from proxies ignoring response_format, triggers fallback."""
        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="## markdown not json"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        instructor_result = Person(name="Bob", age=25)
        completion = SimpleNamespace(usage=None)
        mock_patched = MagicMock()
        mock_patched.chat.completions.create_with_completion = MagicMock(return_value=(instructor_result, completion))

        with patched_instructor_from_openai(mock_patched):
            result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        assert result.text == instructor_result.model_dump_json()
        # Instructor usage None leaves native billed tokens 10/5.
        assert result.input_tokens == 10
        assert result.output_tokens == 5

    async def test_both_usages_none_preserves_none_not_zero(self, backend, sync_to_thread):
        """Native/Instructor usage both None stays untracked None, never literal 0."""
        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        # Native non-JSON HTTP 200 triggers fallback with missing usage.
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="## markdown not json"))],
            usage=None,
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        instructor_result = Person(name="Bob", age=25)
        completion = SimpleNamespace(usage=None)
        mock_patched = MagicMock()
        mock_patched.chat.completions.create_with_completion = MagicMock(return_value=(instructor_result, completion))

        with patched_instructor_from_openai(mock_patched):
            result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        assert result.text == instructor_result.model_dump_json()
        assert result.input_tokens is None
        assert result.output_tokens is None

    async def test_valid_json_satisfying_schema_no_fallback(self, backend, sync_to_thread):
        """Native schema-valid HTTP 200 JSON is accepted without fallback."""
        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        import json

        valid = json.dumps({"name": "Alice", "age": 30})
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=valid))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        with patched_instructor_from_openai() as mock_from_openai:
            result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        assert result.text == valid
        assert result.input_tokens == 20
        assert result.output_tokens == 10
        mock_from_openai.assert_not_called()

    async def test_dict_schema_non_json_falls_back_to_json_object_with_token_merge(self, backend, sync_to_thread):
        """Native dict-schema non-JSON HTTP 200 falls back to json_object and merges native tokens."""
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        fb = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"key": "value"}'))],
            usage=SimpleNamespace(prompt_tokens=30, completion_tokens=15),
        )
        backend._openai_client.chat.completions.create = MagicMock(return_value=fb)

        result = await backend.generate(TextGenerationRequest(prompt="x", response_schema={"type": "object"}))

        assert result.text == '{"key": "value"}'
        # Merge native 10/5 with json_object fallback 30/15.
        assert result.input_tokens == 40
        assert result.output_tokens == 20
        call_kwargs = backend._openai_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["response_format"] == {"type": "json_object"}

    async def test_dict_schema_valid_json_no_fallback(self, backend, sync_to_thread):
        """Native dict-schema valid JSON skips fallback even if declared types differ."""
        import json

        violating = json.dumps({"key": "value", "age": "thirty"})
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=violating))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)
        backend._openai_client.chat.completions.create = MagicMock()

        result = await backend.generate(
            TextGenerationRequest(
                prompt="x",
                response_schema={"type": "object", "properties": {"age": {"type": "integer"}}},
            )
        )

        assert result.text == violating
        backend._openai_client.chat.completions.create.assert_not_called()

    async def test_coercible_value_does_not_trigger_fallback(self, backend, sync_to_thread):
        """Native HTTP 200 coercible values, e.g. integer fields containing "30", skip fallback.

        Ark native response_format omits strict, so strict=False revalidation accepts coercion,
        avoiding extra paid fallback calls for provider-accepted valid responses.
        """
        import json

        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        coercible = json.dumps({"name": "Alice", "age": "30"})
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=coercible))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        with patched_instructor_from_openai() as mock_from_openai:
            result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        assert result.text == coercible
        assert result.input_tokens == 20
        assert result.output_tokens == 10
        mock_from_openai.assert_not_called()

    async def test_empty_choices_on_200_falls_back(self, backend, sync_to_thread):
        """Native HTTP 200 empty choices from relays/moderation catches parsing failures and falls back without escape."""
        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        mock_resp = SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        instructor_result = Person(name="Bob", age=25)
        completion = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=80, completion_tokens=30))
        mock_patched = MagicMock()
        mock_patched.chat.completions.create_with_completion = MagicMock(return_value=(instructor_result, completion))

        with patched_instructor_from_openai(mock_patched):
            result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        # Parsing exceptions enter fallback validation; do not merge native tokens because parsing failed.
        assert result.text == instructor_result.model_dump_json()
        assert result.input_tokens == 80
        assert result.output_tokens == 30

    async def test_fallback_transient_error_does_not_replay_native_call(self, backend, sync_to_thread):
        """Transient fallback failures retry only within fallback, never replay successful paid native calls."""
        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="非 JSON 散文"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        with (
            patch(
                "lib.backends.text_backends.instructor_support.instructor_fallback_sync",
                side_effect=ConnectionError("503 service unavailable"),
            ) as mock_instructor,
            bounded_poll_clock(),
            pytest.raises(ConnectionError),
        ):
            await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        # Native HTTP 200 occurs once; fallback exhausts its own three retries.
        backend._test_client.chat.completions.create.assert_called_once()
        assert mock_instructor.call_count == 3


class TestBaseUrl:
    def test_custom_base_url_passes_to_both_clients(self):
        url = "https://ark.cn-beijing.volces.com/api/plan/v3"
        with (
            captured_ark_clients("lib.backends.text_backends.ark") as ark_created,
            _recorded_openai_clients() as oa_created,
        ):
            ArkTextBackend(api_key="k", base_url=url)
        assert ark_created == [{"api_key": "k", "base_url": url}]
        assert oa_created == [{"base_url": url, "api_key": "k"}]

    def test_default_base_url_keeps_ark_v3(self):
        from lib.backends.ark_shared import ARK_BASE_URL

        with (
            captured_ark_clients("lib.backends.text_backends.ark") as ark_created,
            _recorded_openai_clients() as oa_created,
        ):
            ArkTextBackend(api_key="k")
        assert ark_created == [{"api_key": "k", "base_url": ARK_BASE_URL}]
        assert oa_created == [{"base_url": ARK_BASE_URL, "api_key": "k"}]
