"""GeminiTextBackend tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from google.genai.types import GenerateContentResponseUsageMetadata
from pydantic import BaseModel

from lib.backends.text_backends.base import (
    StructuredOutputExhaustedError,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)
from lib.backends.text_backends.gemini import GeminiTextBackend
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, request_json
from tests.unit.lib.backends.text_backends.response_schema_support import (
    response_schema_models,
    response_schema_params,
)


@pytest.fixture
def mock_genai():
    with patch("lib.backends.text_backends.gemini.genai") as m:
        yield m


class TestProperties:
    def test_name(self, mock_genai):
        b = GeminiTextBackend(api_key="k")
        assert b.name == "gemini"
        # AI Studio construction passes api_key unchanged to genai.Client; absent base_url omits http_options.
        mock_genai.Client.assert_called_once_with(api_key="k", http_options=None)

    def test_default_model(self, mock_genai):
        b = GeminiTextBackend(api_key="k")
        assert b.model == "gemini-3-flash-preview"

    def test_custom_model(self, mock_genai):
        b = GeminiTextBackend(api_key="k", model="custom")
        assert b.model == "custom"

    def test_capabilities(self, mock_genai):
        b = GeminiTextBackend(api_key="k")
        assert b.capabilities == {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    def test_no_api_key_raises(self, mock_genai):
        with pytest.raises(ValueError, match="API Key"):
            GeminiTextBackend()


class TestGenerate:
    @pytest.fixture
    def backend(self, mock_genai):
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        b = GeminiTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    async def test_plain_text(self, backend):
        mock_resp = SimpleNamespace(
            text="  generated text  ",
            usage_metadata=GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=5),
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert isinstance(result, TextGenerationResult)
        assert result.text == "generated text"
        assert result.provider == "gemini"
        assert result.input_tokens == 10
        assert result.output_tokens == 5

    async def test_cache_hits_and_thinking_tokens_are_reported(self, backend):
        """prompt_token_count includes cache hits, reported separately; reasoning tokens count as output."""
        mock_resp = SimpleNamespace(
            text="ok",
            usage_metadata=GenerateContentResponseUsageMetadata(
                prompt_token_count=1000,
                cached_content_token_count=700,
                candidates_token_count=50,
                thoughts_token_count=30,
            ),
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert (result.input_tokens, result.cache_read_tokens, result.output_tokens) == (1000, 700, 80)

    async def test_structured_output_passes_schema(self, backend):
        mock_resp = SimpleNamespace(
            text='{"key": "value"}',
            usage_metadata=GenerateContentResponseUsageMetadata(prompt_token_count=20, candidates_token_count=10),
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        result = await backend.generate(TextGenerationRequest(prompt="gen json", response_schema=schema))

        assert result.text == '{"key": "value"}'
        call_kwargs = backend._test_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        assert config["response_mime_type"] == "application/json"
        assert config["response_schema"] == schema
        assert "response_json_schema" not in config

    async def test_structured_truncation_raises(self, backend):
        """MAX_TOKENS structured truncation raises TextOutputTruncatedError (docs/adr/0044)."""
        from lib.backends.text_backends.base import TextOutputTruncatedError

        mock_resp = SimpleNamespace(
            text='{"key": "value"}',
            usage_metadata=GenerateContentResponseUsageMetadata(prompt_token_count=20, candidates_token_count=10),
            candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        with pytest.raises(TextOutputTruncatedError) as exc_info:
            await backend.generate(TextGenerationRequest(prompt="gen", response_schema=schema))

        assert exc_info.value.provider == "gemini"

    async def test_free_text_truncation_only_warns(self, backend, caplog):
        """Truncated free text without response_schema warns/marks results without raising."""
        import logging

        mock_resp = SimpleNamespace(
            text="partial",
            usage_metadata=GenerateContentResponseUsageMetadata(prompt_token_count=20, candidates_token_count=10),
            candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        with caplog.at_level(logging.WARNING, logger="lib.backends.text_backends.base"):
            result = await backend.generate(TextGenerationRequest(prompt="gen"))

        assert result.text == "partial"
        assert result.truncated is True
        assert any("被截断" in r.message for r in caplog.records)

    async def test_structured_output_pydantic_class_resolved_to_response_schema(self, backend):
        """Resolve Pydantic classes to dicts through response_schema, wire responseSchema.

        Mature responseSchema is widely forwarded by proxies for upstream constrained decoding.
        Newer responseJsonSchema is often unknown to proxy request structs and silently dropped,
        leaving unconstrained JSON generation. Use responseSchema uniformly, matching dict inputs.
        """
        from pydantic import BaseModel

        class MyModel(BaseModel):
            name: str

        mock_resp = SimpleNamespace(
            text='{"name": "test"}',
            usage_metadata=GenerateContentResponseUsageMetadata(prompt_token_count=20, candidates_token_count=10),
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        await backend.generate(TextGenerationRequest(prompt="gen", response_schema=MyModel))

        call_kwargs = backend._test_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        assert config["response_mime_type"] == "application/json"
        assert "response_json_schema" not in config
        js = config["response_schema"]
        assert js["type"] == "object"
        assert js["properties"]["name"]["type"] == "string"
        assert js["required"] == ["name"]

    def test_episode_script_integer_enum_stringified(self, backend):
        """Regression: integer duration_seconds enums convert to string enums through responseSchema.

        types.Schema accepts string enums only; unchanged integers make real SDK conversion raise
        "Input should be a valid string". String enums preserve exact constrained sets, while
        parser _duration_literal mechanically converts back to int.
        """
        from lib.script.script_models import build_episode_script_model

        config = backend._build_config(build_episode_script_model("narration", [4, 6, 8]), None)

        assert "response_json_schema" not in config
        seg_props = config["response_schema"]["properties"]["segments"]["items"]["properties"]
        assert seg_props["duration_seconds"]["enum"] == ["4", "6", "8"]
        assert seg_props["duration_seconds"]["type"] == "string"

    def test_single_value_duration_const_normalized_to_enum(self, backend):
        """Single supported_durations becomes const, unsupported by types.Schema; normalize to singleton string enums, retaining hard generation constraints."""
        from lib.script.script_models import build_episode_script_model

        config = backend._build_config(build_episode_script_model("narration", [8]), None)
        ds = config["response_schema"]["properties"]["segments"]["items"]["properties"]["duration_seconds"]
        assert "const" not in ds
        assert ds["enum"] == ["8"]
        assert ds["type"] == "string"

    def test_const_to_enum_distinguishes_keyword_field_name_and_data(self, backend):
        """Distinguish const as schema keyword (normalize), field name (its value remains a schema), and instance data (unchanged).

        Repository const comes only from scalar singleton-duration Literals. Position awareness
        keeps properties-map keys as names while recursively normalizing real const in subschemas;
        const/default keyword values are data, never recursed into.
        """
        schema = {
            "type": "object",
            "properties": {
                "duration_seconds": {"const": 8, "type": "integer"},  # const keyword → normalize.
                "const": {"type": "string"},  # const field name → unchanged, with no const in its value.
                "default": {"const": 6, "type": "integer"},  # default field → subschema; normalize const.
                "with_default": {"type": "object", "default": {"const": 42}},  # default keyword data → unchanged.
                "obj_const": {"const": {"const": 5}},  # Nonscalar const → unchanged.
            },
        }
        props = backend._build_config(schema, None)["response_schema"]["properties"]
        assert props["duration_seconds"] == {"type": "string", "enum": ["8"]}
        assert props["const"] == {"type": "string"}
        assert props["default"] == {"type": "string", "enum": ["6"]}
        assert props["with_default"]["default"] == {"const": 42}
        assert props["obj_const"] == {"const": {"const": 5}}

    def test_all_script_schemas_accepted_by_google_genai_schema(self, backend):
        """Integration regression: all script-schema factories through _build_config must satisfy real types.Schema.

        Mocking generate_content skips SDK conversion and hides integer-enum/const incompatibility:
        "Input should be a valid string" occurs before requests, failing whole episodes. Exercise
        real SDK type validation to close this gap.
        """
        from google.genai import types as gtypes

        from lib.script.script_models import (
            AdReferenceFlatScript,
            ReferencePromptAuthoringFlatScript,
            build_drama_normalized_script_model,
            build_episode_script_model,
            build_reference_units_script_plan_model,
        )

        schemas = [
            build_episode_script_model(mode, durations)
            for mode in ("narration", "drama", "ad")
            for durations in ([4, 6, 8], [8])
        ]
        schemas += [
            AdReferenceFlatScript,
            build_drama_normalized_script_model([4, 6, 8]),
            build_drama_normalized_script_model([8]),
            build_reference_units_script_plan_model([4, 6, 8]),
            # Singleton const uses a different normalization path from multivalue enums.
            build_reference_units_script_plan_model([8]),
            # Actual static prompt_authoring response schemas, without enum narrowing, use this channel too.
            ReferencePromptAuthoringFlatScript,
        ]
        assert schemas, "schema 清单为空时下面的循环形同虚设"
        for schema in schemas:
            config = backend._build_config(schema, None)
            # No raise proves converted string-enum/no-const dicts satisfy google-genai types.Schema.
            validated = gtypes.Schema.model_validate(config["response_schema"])
            assert validated.type == gtypes.Type.OBJECT

    async def test_system_prompt(self, backend):
        mock_resp = SimpleNamespace(
            text="output",
            usage_metadata=None,
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        result = await backend.generate(TextGenerationRequest(prompt="hello", system_prompt="You are X."))

        assert result.text == "output"
        assert result.input_tokens is None
        call_kwargs = backend._test_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        assert config["system_instruction"] == "You are X."

    async def test_no_usage_metadata(self, backend):
        mock_resp = SimpleNamespace(text="output", usage_metadata=None)
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        result = await backend.generate(TextGenerationRequest(prompt="hi"))
        assert result.input_tokens is None
        assert result.output_tokens is None

    async def test_max_output_tokens_in_config(self, backend):
        """Inject max_output_tokens into Gemini config dicts."""
        mock_resp = SimpleNamespace(text="x", usage_metadata=None)
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        await backend.generate(TextGenerationRequest(prompt="hi", max_output_tokens=32000))

        call_kwargs = backend._test_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        assert config["max_output_tokens"] == 32000

    async def test_no_max_output_tokens_means_no_config_key(self, backend):
        """Omitted max_output_tokens leaves no config key."""
        mock_resp = SimpleNamespace(text="x", usage_metadata=None)
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        await backend.generate(TextGenerationRequest(prompt="hi"))

        call_kwargs = backend._test_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        assert config is None or "max_output_tokens" not in config


class _OverviewModel(BaseModel):
    genre: str
    theme: str


def _resp(text: str, input_tokens: int | None = None, output_tokens: int | None = None) -> SimpleNamespace:
    usage = (
        GenerateContentResponseUsageMetadata(prompt_token_count=input_tokens, candidates_token_count=output_tokens)
        if input_tokens is not None or output_tokens is not None
        else None
    )
    return SimpleNamespace(text=text, usage_metadata=usage)


_PROSE = "经仔细阅读，该剧本讲述了一个都市逆袭故事。"
_VALID_JSON = '{"genre": "都市", "theme": "逆袭"}'


class TestStructuredFallback:
    """Fallback when proxy HTTP 200 ignores response_schema and returns prose/invalid JSON."""

    @pytest.fixture
    def backend(self, mock_genai):
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        b = GeminiTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    async def test_prose_triggers_prompt_fallback_and_succeeds(self, backend):
        """Prose triggers schema-in-prompt fallback without structured wire parameters, merging token usage."""
        gc = AsyncMock(side_effect=[_resp(_PROSE, 20, 10), _resp(_VALID_JSON, 30, 15)])
        backend._test_client.aio.models.generate_content = gc

        result = await backend.generate(TextGenerationRequest(prompt="分析剧本", response_schema=_OverviewModel))

        assert gc.call_count == 2
        fb_config = gc.call_args_list[1].kwargs.get("config")
        assert fb_config is None or (
            "response_schema" not in fb_config
            and "response_json_schema" not in fb_config
            and "response_mime_type" not in fb_config
        )
        fb_prompt = gc.call_args_list[1].kwargs["contents"][-1]
        assert "分析剧本" in fb_prompt
        assert "JSON Schema" in fb_prompt
        assert '"genre"' in fb_prompt
        # Return normalized validated JSON, guaranteeing downstream model_validate_json success.
        assert _OverviewModel.model_validate_json(result.text) == _OverviewModel(genre="都市", theme="逆袭")
        assert result.provider == "gemini"
        assert result.model == backend.model
        assert result.input_tokens == 50
        assert result.output_tokens == 25

    async def test_fallback_strips_code_fences(self, backend):
        """Strip Markdown fences before fallback validation."""
        fenced = f"```json\n{_VALID_JSON}\n```"
        gc = AsyncMock(side_effect=[_resp(_PROSE), _resp(fenced)])
        backend._test_client.aio.models.generate_content = gc

        result = await backend.generate(TextGenerationRequest(prompt="p", response_schema=_OverviewModel))

        assert _OverviewModel.model_validate_json(result.text).genre == "都市"
        # Both native/fallback lack usage: retain None, never 0.
        assert result.input_tokens is None
        assert result.output_tokens is None

    async def test_fallback_retries_with_error_feedback(self, backend):
        """Initial fallback schema violations retry once with validation feedback."""
        gc = AsyncMock(
            side_effect=[
                _resp(_PROSE, 10, 5),
                _resp('{"genre": "都市"}', 10, 5),  # Missing required theme.
                _resp(_VALID_JSON, 10, 5),
            ]
        )
        backend._test_client.aio.models.generate_content = gc

        result = await backend.generate(TextGenerationRequest(prompt="p", response_schema=_OverviewModel))

        assert gc.call_count == 3
        retry_prompt = gc.call_args_list[2].kwargs["contents"][-1]
        assert "不符合" in retry_prompt
        assert _OverviewModel.model_validate_json(result.text).theme == "逆袭"
        assert result.input_tokens == 30
        assert result.output_tokens == 15

    async def test_fallback_exhausted_raises_structured_output_exhausted(self, backend):
        """Exhausted fallback retries become user-readable StructuredOutputExhaustedError, never raw Pydantic messages."""
        gc = AsyncMock(side_effect=[_resp(_PROSE), _resp(_PROSE), _resp(_PROSE)])
        backend._test_client.aio.models.generate_content = gc

        with pytest.raises(StructuredOutputExhaustedError, match="结构化输出能力不足") as exc_info:
            await backend.generate(TextGenerationRequest(prompt="p", response_schema=_OverviewModel))

        assert gc.call_count == 3
        assert exc_info.value.provider == "gemini"
        assert exc_info.value.model == "gemini-3-flash-preview"
        # reason names attempt counts and final validation failure.
        assert exc_info.value.reason == "prompt 注入降级 2 次尝试后输出仍不合规（1 处字段不符（<root>））"

    async def test_fallback_logs_raw_model_output(self, backend, caplog):
        """Fallback-trigger warnings log raw model output for retrospective diagnosis."""
        gc = AsyncMock(side_effect=[_resp(_PROSE), _resp(_VALID_JSON)])
        backend._test_client.aio.models.generate_content = gc

        with caplog.at_level("WARNING", logger="lib.backends.text_backends.gemini"):
            await backend.generate(TextGenerationRequest(prompt="p", response_schema=_OverviewModel))

        assert any(_PROSE in record.getMessage() for record in caplog.records)

    async def test_valid_schema_json_no_fallback(self, backend):
        """Valid schema-compliant JSON skips fallback and uses one call."""
        gc = AsyncMock(return_value=_resp(_VALID_JSON, 20, 10))
        backend._test_client.aio.models.generate_content = gc

        result = await backend.generate(TextGenerationRequest(prompt="p", response_schema=_OverviewModel))

        assert gc.call_count == 1
        assert result.text == _VALID_JSON

    async def test_dict_schema_prose_fallback_returns_stripped_json(self, backend):
        """Dict schemas have no Pydantic validator; fallback admits syntactically valid JSON only."""
        schema = {"type": "object", "properties": {"k": {"type": "string"}}}
        gc = AsyncMock(side_effect=[_resp(_PROSE), _resp('```json\n{"k": "v"}\n```')])
        backend._test_client.aio.models.generate_content = gc

        result = await backend.generate(TextGenerationRequest(prompt="p", response_schema=schema))

        assert result.text == '{"k": "v"}'

    async def test_fallback_transient_error_does_not_replay_native_call(self, backend):
        """Transient fallback errors retry within fallback only, never replay successful paid native calls."""
        transient = ConnectionError("503 service unavailable")
        gc = AsyncMock(side_effect=[_resp(_PROSE), transient, transient, transient])
        backend._test_client.aio.models.generate_content = gc

        with bounded_poll_clock(), pytest.raises(ConnectionError):
            await backend.generate(TextGenerationRequest(prompt="p", response_schema=_OverviewModel))

        # One native success plus three exhausted fallback retries, without native replay.
        assert gc.call_count == 4


def _keys_anywhere(node: object, names: set[str]) -> list[str]:
    """Collect specified keys recursively, including property-name positions, to assert total absence."""
    found: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in names:
                found.append(k)
            found.extend(_keys_anywhere(v, names))
    elif isinstance(node, list):
        for item in node:
            found.extend(_keys_anywhere(item, names))
    return found


class TestAdditionalPropertiesStripped:
    """Gemini Developer API responseSchema rejects additionalProperties with HTTP 400 INVALID_ARGUMENT."""

    @pytest.fixture
    def backend(self, mock_genai):
        mock_genai.Client.return_value = MagicMock()
        return GeminiTextBackend(api_key="k")

    def test_response_schema_models_carry_additional_properties_before_conversion(self):
        """Verify raw schemas contain additionalProperties, or later parametrized assertions would be vacuous."""
        assert any(_keys_anywhere(m.model_json_schema(), {"additionalProperties"}) for m in response_schema_models())

    @pytest.mark.parametrize("model", response_schema_params())
    def test_every_response_schema_model_has_no_additional_properties(self, backend, model):
        config = backend._build_config(model, None)
        assert _keys_anywhere(config["response_schema"], {"additionalProperties", "additional_properties"}) == []

    def test_strips_at_every_nesting_level_including_array_items_and_union_members(self, backend):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": False, "properties": {"a": {"type": "string"}}},
                },
                "either": {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}]},
            },
        }
        out = backend._build_config(schema, None)["response_schema"]
        assert _keys_anywhere(out, {"additionalProperties"}) == []
        assert out["properties"]["items"]["items"]["properties"] == {"a": {"type": "string"}}
        assert out["properties"]["either"]["anyOf"][0] == {"type": "object"}

    def test_keeps_property_named_additional_properties_and_instance_data(self, backend):
        """Strip schema keywords only; preserve same-named fields and enum/default/const instance data."""
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "additionalProperties": {"type": "string", "description": "字段名恰好叫 additionalProperties"},
                "nested": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"additionalProperties": {"type": "integer"}},
                },
                "with_default": {"type": "object", "default": {"additionalProperties": False}},
                "with_enum": {"enum": ["additionalProperties", "other"]},
                "with_const": {"const": {"additionalProperties": 1}},
                "with_examples": {"type": "object", "examples": [{"additionalProperties": False}]},
            },
            "required": ["additionalProperties"],
        }
        out = backend._build_config(schema, None)["response_schema"]
        props = out["properties"]
        assert "additionalProperties" not in {k for k in out if k != "properties"}
        assert props["additionalProperties"] == {"type": "string", "description": "字段名恰好叫 additionalProperties"}
        assert props["nested"] == {"type": "object", "properties": {"additionalProperties": {"type": "integer"}}}
        assert props["with_default"]["default"] == {"additionalProperties": False}
        assert props["with_enum"]["enum"] == ["additionalProperties", "other"]
        assert props["with_const"]["const"] == {"additionalProperties": 1}
        assert props["with_examples"]["examples"] == [{"additionalProperties": False}]
        assert out["required"] == ["additionalProperties"]

    async def test_request_body_response_schema_has_no_additional_properties(self):
        """Real google-genai serialized generationConfig.responseSchema omits the field.

        Installed aiohttp makes google-genai default to it, bypassing respx. Inject
        ``httpx_async_client`` to route all SDK/fallback requests through transport-level respx,
        rejecting every undeclared request.
        """
        from google import genai as real_genai

        reply = {
            "candidates": [{"content": {"parts": [{"text": "{}"}], "role": "model"}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        }
        backend = GeminiTextBackend(api_key="k")
        with capture_http() as router:
            route = router.post(url__regex=r":generateContent$").mock(return_value=httpx.Response(200, json=reply))
            async with httpx.AsyncClient() as http_client:
                backend._client = real_genai.Client(api_key="k", http_options={"httpx_async_client": http_client})
                for model in response_schema_models():
                    route.calls.reset()
                    # Empty objects fail validation and enter fallback; observe first native structured requests only.
                    with pytest.raises(StructuredOutputExhaustedError):
                        await backend.generate(TextGenerationRequest(prompt="x", response_schema=model))
                    assert route.called, model.__name__
                    response_schema = request_json(route.calls[0].request)["generationConfig"]["responseSchema"]
                    assert response_schema["type"] == "OBJECT"
                    assert _keys_anywhere(response_schema, {"additional_properties", "additionalProperties"}) == [], (
                        model.__name__
                    )
