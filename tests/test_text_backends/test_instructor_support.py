"""Unit tests for the instructor_support module."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import BaseModel

from lib.text_backends.instructor_support import (
    generate_structured_via_instructor,
    instructor_fallback_async,
    instructor_fallback_sync,
)


class SampleModel(BaseModel):
    name: str
    age: int


class TestGenerateStructuredViaInstructor:
    def test_returns_json_and_tokens(self):
        """Returns JSON text and token counts correctly."""
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
        """Passes mode and max_retries arguments correctly."""
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

            # Verify from_openai was called with the correct mode
            mock_instructor.from_openai.assert_called_once_with(mock_client, mode=Mode.MD_JSON)
            # Verify create_with_completion was called with the correct parameters
            mock_patched.chat.completions.create_with_completion.assert_called_once_with(
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                max_retries=3,
            )

    def test_handles_none_usage(self):
        """Returns None token counts when completion.usage is None."""
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


class TestInstructorFallbackSync:
    """Tests for the instructor_fallback_sync higher-level function."""

    def test_pydantic_schema_uses_instructor(self):
        """Pydantic schema takes the instructor path and returns the correct TextGenerationResult."""
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
        """Dict schema takes the json_object path."""
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


class TestInstructorFallbackAsync:
    """Tests for the instructor_fallback_async higher-level function."""

    async def test_pydantic_schema_uses_instructor_async(self):
        """Pydantic schema takes the async instructor path."""
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
        """Dict schema takes the async json_object path."""
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
