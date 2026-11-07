"""TextBackend Protocol + data classes tests."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from lib.backends.text_backends.base import (
    TEXT_TASK_TIERS,
    VISION_REQUIRED_TASKS,
    ImageInput,
    TextBackend,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    TextTaskTier,
    TextTaskType,
    model_facing_response_model,
    resolve_schema,
)
from tests.unit.lib.backends.text_backends.response_schema_support import (
    _class_docstring_texts,
    response_schema_models,
    response_schema_params,
)


class TestTextCapability:
    def test_values(self):
        assert TextCapability.TEXT_GENERATION == "text_generation"
        assert TextCapability.STRUCTURED_OUTPUT == "structured_output"
        assert TextCapability.VISION == "vision"

    def test_is_str_enum(self):
        assert isinstance(TextCapability.TEXT_GENERATION, str)


class TestTextTaskType:
    def test_values(self):
        assert TextTaskType.SCRIPT == "script"
        assert TextTaskType.OVERVIEW == "overview"
        assert TextTaskType.STYLE_ANALYSIS == "style"


class TestTextTaskTiers:
    def test_every_task_type_is_mapped(self):
        """Exhaustive validation: new TextTaskType members must register explicitly in TEXT_TASK_TIERS."""
        assert set(TEXT_TASK_TIERS) == set(TextTaskType)

    def test_tier_assignments(self):
        assert TEXT_TASK_TIERS[TextTaskType.SCRIPT] is TextTaskTier.COMPLEX
        assert TEXT_TASK_TIERS[TextTaskType.OVERVIEW] is TextTaskTier.SIMPLE
        assert TEXT_TASK_TIERS[TextTaskType.STYLE_ANALYSIS] is TextTaskTier.SIMPLE

    def test_vision_required_tasks_are_valid_members(self):
        assert set(TextTaskType) >= VISION_REQUIRED_TASKS
        assert TextTaskType.STYLE_ANALYSIS in VISION_REQUIRED_TASKS


class TestImageInput:
    def test_path_only(self):
        inp = ImageInput(path=Path("/tmp/img.png"))
        assert inp.path == Path("/tmp/img.png")
        assert inp.url is None

    def test_url_only(self):
        inp = ImageInput(url="https://example.com/img.png")
        assert inp.path is None
        assert inp.url == "https://example.com/img.png"


class TestTextGenerationRequest:
    def test_minimal(self):
        req = TextGenerationRequest(prompt="hello")
        assert req.prompt == "hello"
        assert req.response_schema is None
        assert req.images is None
        assert req.system_prompt is None

    def test_full(self):
        req = TextGenerationRequest(
            prompt="analyze",
            response_schema={"type": "object"},
            images=[ImageInput(path=Path("/tmp/img.png"))],
            system_prompt="You are a helpful assistant.",
        )
        assert req.response_schema == {"type": "object"}
        assert len(req.images) == 1
        assert req.system_prompt == "You are a helpful assistant."


class TestTextGenerationResult:
    def test_minimal(self):
        result = TextGenerationResult(text="output", provider="gemini", model="flash")
        assert result.text == "output"
        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_with_tokens(self):
        result = TextGenerationResult(
            text="output",
            provider="ark",
            model="seed",
            input_tokens=100,
            output_tokens=50,
        )
        assert result.input_tokens == 100
        assert result.output_tokens == 50


def _field_descriptions(raw: dict) -> set[str]:
    """Field-level ``description`` values in default Pydantic schemas."""
    nodes = [raw, *raw.get("$defs", {}).values()]
    return {
        prop["description"]
        for node in nodes
        for prop in node.get("properties", {}).values()
        if isinstance(prop, dict) and "description" in prop
    }


def _description_values(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "description" and isinstance(v, str):
                found.append(v)
            found.extend(_description_values(v))
    elif isinstance(node, list):
        for item in node:
            found.extend(_description_values(item))
    return found


class TestResolveSchema:
    def test_dict_without_refs_unchanged(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        assert resolve_schema(schema) == schema

    def test_inlines_refs(self):
        schema = {
            "$defs": {"Inner": {"type": "object", "properties": {"x": {"type": "integer"}}}},
            "type": "object",
            "properties": {"child": {"$ref": "#/$defs/Inner"}},
        }
        result = resolve_schema(schema)
        assert "$defs" not in result
        assert "$ref" not in str(result)
        assert result["properties"]["child"]["properties"]["x"]["type"] == "integer"

    def test_pydantic_class(self):

        from pydantic import BaseModel

        class Item(BaseModel):
            value: int

        class Container(BaseModel):
            items: list[Item]

        result = resolve_schema(Container)
        assert "$ref" not in str(result)
        assert "$defs" not in result
        items_schema = result["properties"]["items"]["items"]
        assert items_schema["properties"]["value"]["type"] == "integer"

    def test_circular_ref_raises(self):
        schema = {
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {"child": {"$ref": "#/$defs/Node"}},
                },
            },
            "type": "object",
            "properties": {"root": {"$ref": "#/$defs/Node"}},
        }

        with pytest.raises(ValueError, match="循环引用"):
            resolve_schema(schema)

    def test_preserves_extra_keys_on_ref(self):
        schema = {
            "$defs": {"Inner": {"type": "object", "properties": {"x": {"type": "integer"}}}},
            "type": "object",
            "properties": {"child": {"$ref": "#/$defs/Inner", "description": "A child"}},
        }
        result = resolve_schema(schema)
        assert result["properties"]["child"]["description"] == "A child"
        assert result["properties"]["child"]["type"] == "object"

    def test_pydantic_class_drops_class_docstrings_keeps_field_descriptions(self):
        from enum import Enum

        from pydantic import ConfigDict, Field

        class Tone(Enum):
            """枚举实现说明"""

            CALM = "calm"

        class Inner(BaseModel):
            """构图信息"""

            x: int = Field(description="横坐标")

        class Explicit(BaseModel):
            """实现说明"""

            model_config = ConfigDict(json_schema_extra={"description": "显式给模型的说明"})

        class Outer(BaseModel):
            """顶层实现说明：ADR 编号、迁移口径"""

            # Field descriptions equal referenced model docstrings: Pydantic retains definitions only, but stripping definitions must preserve field annotations.
            inner: Inner = Field(description="构图信息")
            bare: Inner
            tone: Tone
            explicit: Explicit

        result = resolve_schema(Outer)
        assert "description" not in result
        props = result["properties"]
        assert props["inner"]["description"] == "构图信息"
        assert props["inner"]["properties"]["x"]["description"] == "横坐标"
        assert "description" not in props["bare"]
        assert "description" not in props["tone"]
        assert props["explicit"]["description"] == "显式给模型的说明"

    def test_response_schema_models_carry_docstrings_and_field_descriptions_by_default(self):
        """Verify preconditions: default schemas contain class docs/field descriptions, or later parametrized assertions would be vacuous."""
        models = response_schema_models()
        assert any(_class_docstring_texts(model) for model in models)
        assert any(_field_descriptions(model.model_json_schema()) for model in models)

    @pytest.mark.parametrize("model", response_schema_params())
    def test_response_schema_models_drop_class_docstrings_keep_field_descriptions(self, model: type[BaseModel]):
        """Class docs explain implementation to maintainers; field descriptions instruct models."""
        raw = model.model_json_schema()
        field_descriptions = _field_descriptions(raw)
        resolved = resolve_schema(model)
        descriptions = _description_values(resolved)

        # Neither root nor inlined nested models retain class descriptions.
        assert "description" not in resolved
        assert _class_docstring_texts(model).isdisjoint(descriptions)
        assert field_descriptions <= set(descriptions)


class TestModelFacingResponseModel:
    def test_schema_drops_class_docstrings_validation_and_dump_unchanged(self):
        from pydantic import Field

        class Inner(BaseModel):
            """嵌套实现说明"""

            x: int = Field(description="横坐标")

        class Outer(BaseModel):
            """顶层实现说明"""

            inner: Inner

        facing = model_facing_response_model(Outer)
        schema = facing.model_json_schema()

        assert facing.__name__ == "Outer"
        assert facing.__doc__ is None
        assert "description" not in schema
        assert "description" not in schema["$defs"]["Inner"]
        assert schema["$defs"]["Inner"]["properties"]["x"]["description"] == "横坐标"
        parsed = facing.model_validate_json('{"inner": {"x": 1}}')
        assert isinstance(parsed, Outer)
        assert parsed.model_dump_json() == Outer(inner=Inner(x=1)).model_dump_json()


class TestTextBackendProtocol:
    def test_satisfies_protocol(self):
        class FakeBackend:
            @property
            def name(self) -> str:
                return "fake"

            @property
            def model(self) -> str:
                return "fake-model"

            @property
            def capabilities(self) -> set[TextCapability]:
                return {TextCapability.TEXT_GENERATION}

            async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
                return TextGenerationResult(text="ok", provider="fake", model="fake-model")

        backend: TextBackend = FakeBackend()
        assert backend.name == "fake"
        assert backend.model == "fake-model"
        assert TextCapability.TEXT_GENERATION in backend.capabilities


class _Person(BaseModel):
    name: str
    age: int


class TestStructuredFallbackReason:
    """Shared pure revalidation used by openai/ark/gemini."""

    def test_valid_json_satisfying_pydantic_returns_none(self):
        import json

        from lib.backends.text_backends.base import structured_fallback_reason

        assert structured_fallback_reason(json.dumps({"name": "A", "age": 30}), _Person) is None

    def test_non_json_returns_reason(self):
        from lib.backends.text_backends.base import structured_fallback_reason

        reason = structured_fallback_reason("## markdown not json", _Person)
        assert reason is not None
        assert "JSON" in reason or "schema" in reason

    def test_schema_violating_json_returns_reason(self):
        import json

        from lib.backends.text_backends.base import structured_fallback_reason

        # Valid JSON but Chinese-string age violates integer constraints.
        violating = json.dumps({"name": "A", "age": "三十"}, ensure_ascii=False)
        assert structured_fallback_reason(violating, _Person) is not None

    def test_coercible_but_non_strict_returns_reason(self):
        import json

        from lib.backends.text_backends.base import structured_fallback_reason

        # strict=True never coerces "30" to 30, detecting unenforced schemas.
        assert structured_fallback_reason(json.dumps({"name": "A", "age": "30"}), _Person) is not None

    def test_strict_false_tolerates_coercible_returns_none(self):
        import json

        from lib.backends.text_backends.base import structured_fallback_reason

        # strict=False, matching nonstrict backends, tolerates coercible "30" without fallback.
        assert structured_fallback_reason(json.dumps({"name": "A", "age": "30"}), _Person, strict=False) is None

    def test_strict_false_still_flags_genuine_violation(self):
        import json

        from lib.backends.text_backends.base import structured_fallback_reason

        # strict=False still falls back for truly invalid output: uncoercible integers plus missing required fields.
        non_coercible = json.dumps({"name": "A", "age": "三十"}, ensure_ascii=False)
        assert structured_fallback_reason(non_coercible, _Person, strict=False) is not None
        assert structured_fallback_reason(json.dumps({"name": "A"}), _Person, strict=False) is not None

    def test_missing_required_field_returns_reason(self):
        import json

        from lib.backends.text_backends.base import structured_fallback_reason

        assert structured_fallback_reason(json.dumps({"name": "A"}), _Person) is not None

    def test_dict_schema_valid_json_returns_none(self):
        import json

        from lib.backends.text_backends.base import structured_fallback_reason

        # Dict schemas validate JSON syntax only, never enforce declared types.
        violating = json.dumps({"name": "A", "age": "thirty"})
        schema = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}
        assert structured_fallback_reason(violating, schema) is None

    def test_dict_schema_non_json_returns_reason(self):
        from lib.backends.text_backends.base import structured_fallback_reason

        assert structured_fallback_reason("not json", {"type": "object"}) is not None

    def test_none_schema_never_triggers_fallback(self):
        from lib.backends.text_backends.base import structured_fallback_reason

        # None response_schema means free text, never mistaken for fallback-worthy output.
        assert structured_fallback_reason("plain text, not json", None) is None
        assert structured_fallback_reason('{"a": 1}', None) is None
        assert structured_fallback_reason("", None) is None


class TestStripLeadingThinkBlock:
    """Leading content <think> blocks: strip one complete opening block only, returning all other forms unchanged."""

    def test_strips_leading_block_and_surrounding_whitespace(self):
        from lib.backends.text_backends.base import strip_leading_think_block

        text = '  <think>\n先想一想。\n</think>\n\n{"name": "A", "age": 30}'
        assert strip_leading_think_block(text) == '{"name": "A", "age": 30}'

    def test_text_without_block_unchanged(self):
        from lib.backends.text_backends.base import strip_leading_think_block

        assert strip_leading_think_block('{"name": "A"}') == '{"name": "A"}'
        assert strip_leading_think_block("") == ""

    def test_unclosed_block_unchanged(self):
        from lib.backends.text_backends.base import strip_leading_think_block

        truncated = "<think>\n想到一半就被截断了"
        assert strip_leading_think_block(truncated) == truncated

    def test_block_not_at_start_unchanged(self):
        from lib.backends.text_backends.base import strip_leading_think_block

        text = '{"name": "A"}<think>x</think>'
        assert strip_leading_think_block(text) == text

    def test_only_first_block_stripped(self):
        from lib.backends.text_backends.base import strip_leading_think_block

        assert strip_leading_think_block("<think>a</think>正文<think>b</think>") == "正文<think>b</think>"


class TestMergeBilledTokens:
    """Merge native fallback-call usage only when either side has a value; both None stay None."""

    def test_both_sides_have_values(self):
        from lib.backends.text_backends.base import merge_billed_tokens

        assert merge_billed_tokens(100, 50) == 150

    def test_only_native_has_value(self):
        from lib.backends.text_backends.base import merge_billed_tokens

        assert merge_billed_tokens(None, 50) == 50

    def test_only_fallback_has_value(self):
        from lib.backends.text_backends.base import merge_billed_tokens

        assert merge_billed_tokens(100, None) == 100

    def test_both_none_stays_none(self):
        from lib.backends.text_backends.base import merge_billed_tokens

        assert merge_billed_tokens(None, None) is None


class TestIsValidJson:
    def test_valid(self):
        from lib.backends.text_backends.base import is_valid_json

        assert is_valid_json('{"a": 1}') is True

    def test_empty_and_whitespace(self):
        from lib.backends.text_backends.base import is_valid_json

        assert is_valid_json("") is False
        assert is_valid_json("   ") is False

    def test_non_json(self):
        from lib.backends.text_backends.base import is_valid_json

        assert is_valid_json("hello") is False


class TestSummarizeValidationError:
    def test_no_raw_input_in_summary(self):
        import json

        from pydantic import ValidationError

        from lib.backends.text_backends.base import summarize_validation_error

        try:
            _Person.model_validate_json(json.dumps({"name": "A", "age": "三十"}, ensure_ascii=False), strict=True)
            raise AssertionError("应抛 ValidationError")
        except ValidationError as exc:
            summary = summarize_validation_error(exc)
        assert "age" in summary
        # Summaries omit original input values.
        assert "三十" not in summary


class TestWarnIfTruncated:
    def test_none_finish_reason_returns_false(self, caplog):
        from lib.backends.text_backends.base import warn_if_truncated

        assert warn_if_truncated(None, provider="x", model="m") is False
        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    def test_normal_stop_returns_false(self, caplog):
        from lib.backends.text_backends.base import warn_if_truncated

        assert warn_if_truncated("stop", provider="x", model="m") is False
        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    def test_length_triggers_warning(self, caplog):
        import logging

        from lib.backends.text_backends.base import warn_if_truncated

        with caplog.at_level(logging.WARNING, logger="lib.backends.text_backends.base"):
            result = warn_if_truncated("length", provider="ark", model="doubao", output_tokens=8192)

        assert result is True
        assert any("被截断" in r.message and "length" in r.message for r in caplog.records)

    def test_max_tokens_variant_triggers_warning(self, caplog):
        import logging

        from lib.backends.text_backends.base import warn_if_truncated

        with caplog.at_level(logging.WARNING, logger="lib.backends.text_backends.base"):
            assert warn_if_truncated("MAX_TOKENS", provider="gemini", model="g") is True


class TestCheckTruncation:
    """Structured truncation becomes a hard error; free text remains warning-only (docs/adr/0044)."""

    def test_structured_truncation_raises(self, caplog):
        import logging

        from lib.backends.text_backends.base import TextOutputTruncatedError, check_truncation

        with (
            caplog.at_level(logging.WARNING, logger="lib.backends.text_backends.base"),
            pytest.raises(TextOutputTruncatedError) as exc_info,
        ):
            check_truncation("length", provider="ark", model="doubao", output_tokens=8192, structured=True)

        exc = exc_info.value
        assert exc.provider == "ark"
        assert exc.model == "doubao"
        assert exc.output_tokens == 8192
        # Identify current models and suggest changing models.
        assert "ark/doubao" in str(exc)
        assert "输出能力更高的文本模型" in str(exc)
        assert "output_tokens=8192" in str(exc)

    def test_structured_truncation_without_output_tokens_omits_placeholder(self):
        """Absent provider usage means output_tokens None; errors must not display output_tokens=None."""

        from lib.backends.text_backends.base import TextOutputTruncatedError, check_truncation

        with pytest.raises(TextOutputTruncatedError) as exc_info:
            check_truncation("length", provider="ark", model="doubao", output_tokens=None, structured=True)

        assert "None" not in str(exc_info.value)

    def test_free_text_truncation_only_warns(self, caplog):
        import logging

        from lib.backends.text_backends.base import check_truncation

        with caplog.at_level(logging.WARNING, logger="lib.backends.text_backends.base"):
            check_truncation("length", provider="ark", model="doubao", structured=False)

        assert any("被截断" in r.message for r in caplog.records)

    def test_no_truncation_structured_does_not_raise(self):
        from lib.backends.text_backends.base import check_truncation

        check_truncation("stop", provider="ark", model="doubao", structured=True)

    def test_none_finish_reason_structured_does_not_raise(self):
        from lib.backends.text_backends.base import check_truncation

        check_truncation(None, provider="ark", model="doubao", structured=True)

    def test_truncated_error_is_non_retryable(self):
        """TextOutputTruncatedError must subclass NonRetryableError, or output_tokens integers coincidentally
        containing 429/500 could trigger transient with_retry_async retries
        (lib/infra/retry.py::NonRetryableError)."""
        from lib.backends.text_backends.base import TextOutputTruncatedError
        from lib.infra.retry import NonRetryableError

        assert issubclass(TextOutputTruncatedError, NonRetryableError)
