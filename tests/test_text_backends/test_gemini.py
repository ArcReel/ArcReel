"""GeminiTextBackend tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)
from lib.text_backends.gemini import GeminiTextBackend


@pytest.fixture
def mock_genai():
    with patch("lib.text_backends.gemini.genai") as m:
        yield m


class TestProperties:
    def test_name(self, mock_genai):
        b = GeminiTextBackend(api_key="k")
        assert b.name == "gemini"

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
            usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5),
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert isinstance(result, TextGenerationResult)
        assert result.text == "generated text"
        assert result.provider == "gemini"
        assert result.input_tokens == 10
        assert result.output_tokens == 5

    async def test_structured_output_passes_schema(self, backend):
        mock_resp = SimpleNamespace(
            text='{"key": "value"}',
            usage_metadata=SimpleNamespace(prompt_token_count=20, candidates_token_count=10),
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        result = await backend.generate(TextGenerationRequest(prompt="gen json", response_schema=schema))

        assert result.text == '{"key": "value"}'
        call_kwargs = backend._test_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        assert config["response_mime_type"] == "application/json"
        assert config["response_json_schema"] == schema

    async def test_structured_output_pydantic_class_resolved_to_json_schema(self, backend):
        """传入 Pydantic 类时解析为 JSON Schema dict 走 response_json_schema。

        google-genai 的 response_schema(types.Schema) 是 OpenAPI 子集，enum 仅支持字符串，
        整数/数字 enum 会在 SDK schema 转换时抛 "Input should be a valid string"。统一走
        response_json_schema（标准 JSON Schema，官方支持数字 enum），与 dict 入参同口径。
        """
        from pydantic import BaseModel

        class MyModel(BaseModel):
            name: str

        mock_resp = SimpleNamespace(
            text='{"name": "test"}',
            usage_metadata=SimpleNamespace(prompt_token_count=20, candidates_token_count=10),
        )
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        await backend.generate(TextGenerationRequest(prompt="gen", response_schema=MyModel))

        call_kwargs = backend._test_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        assert config["response_mime_type"] == "application/json"
        assert "response_schema" not in config
        js = config["response_json_schema"]
        assert js["type"] == "object"
        assert js["properties"]["name"]["type"] == "string"
        assert js["required"] == ["name"]

    async def test_episode_script_integer_enum_routes_to_json_schema(self, backend):
        """回归：duration_seconds 整数 enum 的剧本 schema 必须走 response_json_schema。

        build_episode_script_model 把 duration_seconds 收紧为 Literal[*supported_durations]
        （整数 enum）。若退回 response_schema(types.Schema, enum: list[str]) 会在真实 SDK
        转换时抛 "Input should be a valid string"，整集生成直接失败。
        """
        from lib.script_models import build_episode_script_model

        schema = build_episode_script_model("narration", [4, 6, 8])
        mock_resp = SimpleNamespace(text="{}", usage_metadata=None)
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        await backend.generate(TextGenerationRequest(prompt="x", response_schema=schema))

        config = backend._test_client.aio.models.generate_content.call_args.kwargs["config"]
        assert "response_schema" not in config
        seg_props = config["response_json_schema"]["properties"]["segments"]["items"]["properties"]
        assert seg_props["duration_seconds"]["enum"] == [4, 6, 8]

    def test_single_value_duration_const_normalized_to_enum(self, backend):
        """单值 supported_durations 渲染为 const（不在 response_json_schema 支持特性内），
        归一为单元素 enum 以保留生成层硬约束。"""
        from lib.script_models import build_episode_script_model

        config = backend._build_config(build_episode_script_model("narration", [8]), None)
        ds = config["response_json_schema"]["properties"]["segments"]["items"]["properties"]["duration_seconds"]
        assert "const" not in ds
        assert ds["enum"] == [8]

    def test_const_to_enum_distinguishes_keyword_field_name_and_data(self, backend):
        """区分 const 出现的三种位置：schema 关键字（归一）、字段名（值仍是子 schema）、实例数据（不动）。

        本仓库 const 只来自单值时长 Literal（标量）。位置感知确保：properties 等映射的 key 是字段名，
        其值仍是子 schema（里面真正的 const 照常归一）；const/default 等关键字的值是数据，不递归。
        """
        schema = {
            "type": "object",
            "properties": {
                "duration_seconds": {"const": 8, "type": "integer"},  # const 作关键字 → 归一
                "const": {"type": "string"},  # 字段名为 const → 不动（值无 const）
                "default": {"const": 6, "type": "integer"},  # 字段名为 default → 其值是子 schema，const 照常归一
                "with_default": {"type": "object", "default": {"const": 42}},  # default 作关键字（数据）→ 不动
                "obj_const": {"const": {"const": 5}},  # 非标量 const → 不动
            },
        }
        props = backend._build_config(schema, None)["response_json_schema"]["properties"]
        assert props["duration_seconds"] == {"type": "integer", "enum": [8]}
        assert props["const"] == {"type": "string"}
        assert props["default"] == {"type": "integer", "enum": [6]}
        assert props["with_default"]["default"] == {"const": 42}
        assert props["obj_const"] == {"const": {"const": 5}}

    def test_episode_script_schema_accepted_by_google_genai_jsonschema(self, backend):
        """集成回归：剧本 schema 经 _build_config 产出后必须被 google-genai 真实 JSONSchema 接受。

        mock 掉 generate_content 会让 SDK 的 schema 转换不执行，掩盖整数 enum 与 Gemini
        response_schema 的不兼容。这里直接过真实 SDK 类型校验，堵住该盲区。
        """
        from google.genai import types as gtypes

        from lib.script_models import build_episode_script_model

        for content_mode in ("narration", "drama", "ad"):
            for durations in ([4, 6, 8], [8]):
                config = backend._build_config(build_episode_script_model(content_mode, durations), None)
                # 不抛 = 整数 enum / 归一后单值被 google-genai 的 JSONSchema(enum: list[Any]) 接受
                gtypes.JSONSchema.model_validate(config["response_json_schema"])

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
        """max_output_tokens 注入到 Gemini config 字典。"""
        mock_resp = SimpleNamespace(text="x", usage_metadata=None)
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        await backend.generate(TextGenerationRequest(prompt="hi", max_output_tokens=32000))

        call_kwargs = backend._test_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        assert config["max_output_tokens"] == 32000

    async def test_no_max_output_tokens_means_no_config_key(self, backend):
        """未指定 max_output_tokens 时 config 中不应出现该键。"""
        mock_resp = SimpleNamespace(text="x", usage_metadata=None)
        backend._test_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        await backend.generate(TextGenerationRequest(prompt="hi"))

        call_kwargs = backend._test_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        assert config is None or "max_output_tokens" not in config
