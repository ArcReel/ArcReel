"""GeminiTextBackend tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

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

    def test_episode_script_integer_enum_routes_to_json_schema(self, backend):
        """回归：duration_seconds 整数 enum 的剧本 schema 必须走 response_json_schema。

        build_episode_script_model 把 duration_seconds 收紧为 Literal[*supported_durations]
        （整数 enum）。若退回 response_schema(types.Schema, enum: list[str]) 会在真实 SDK
        转换时抛 "Input should be a valid string"，整集生成直接失败。
        """
        from lib.script_models import build_episode_script_model

        config = backend._build_config(build_episode_script_model("narration", [4, 6, 8]), None)

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


class _OverviewModel(BaseModel):
    genre: str
    theme: str


def _resp(text: str, input_tokens: int | None = None, output_tokens: int | None = None) -> SimpleNamespace:
    usage = (
        SimpleNamespace(prompt_token_count=input_tokens, candidates_token_count=output_tokens)
        if input_tokens is not None or output_tokens is not None
        else None
    )
    return SimpleNamespace(text=text, usage_metadata=usage)


_PROSE = "经仔细阅读，该剧本讲述了一个都市逆袭故事。"
_VALID_JSON = '{"genre": "都市", "theme": "逆袭"}'


class TestStructuredFallback:
    """中转 HTTP 200 但不强制 response_json_schema（返回散文/违例 JSON）时的降级路径。"""

    @pytest.fixture
    def backend(self, mock_genai):
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        b = GeminiTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    async def test_prose_triggers_prompt_fallback_and_succeeds(self, backend):
        """散文响应触发降级：schema 注入 prompt、不带 wire 结构化参数重发，token 并账。"""
        gc = AsyncMock(side_effect=[_resp(_PROSE, 20, 10), _resp(_VALID_JSON, 30, 15)])
        backend._test_client.aio.models.generate_content = gc

        result = await backend.generate(TextGenerationRequest(prompt="分析剧本", response_schema=_OverviewModel))

        assert gc.call_count == 2
        fb_config = gc.call_args_list[1].kwargs.get("config")
        assert fb_config is None or ("response_json_schema" not in fb_config and "response_mime_type" not in fb_config)
        fb_prompt = gc.call_args_list[1].kwargs["contents"][-1]
        assert "分析剧本" in fb_prompt
        assert "JSON Schema" in fb_prompt
        assert '"genre"' in fb_prompt
        # 校验通过后返回规范化 JSON，下游 model_validate_json 必定成功
        assert _OverviewModel.model_validate_json(result.text) == _OverviewModel(genre="都市", theme="逆袭")
        assert result.input_tokens == 50
        assert result.output_tokens == 25

    async def test_fallback_strips_code_fences(self, backend):
        """降级输出带 markdown 栅栏时剥离后校验。"""
        fenced = f"```json\n{_VALID_JSON}\n```"
        gc = AsyncMock(side_effect=[_resp(_PROSE), _resp(fenced)])
        backend._test_client.aio.models.generate_content = gc

        result = await backend.generate(TextGenerationRequest(prompt="p", response_schema=_OverviewModel))

        assert _OverviewModel.model_validate_json(result.text).genre == "都市"

    async def test_fallback_retries_with_error_feedback(self, backend):
        """降级首次输出违反 schema 时带错误反馈重试一次。"""
        gc = AsyncMock(
            side_effect=[
                _resp(_PROSE, 10, 5),
                _resp('{"genre": "都市"}', 10, 5),  # 缺必填 theme
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

    async def test_fallback_exhausted_raises_value_error(self, backend):
        """降级重试穷尽后 fail-loud，错误消息面向用户可读（不透传 pydantic 原始串）。"""
        gc = AsyncMock(side_effect=[_resp(_PROSE), _resp(_PROSE), _resp(_PROSE)])
        backend._test_client.aio.models.generate_content = gc

        with pytest.raises(ValueError, match="结构化输出"):
            await backend.generate(TextGenerationRequest(prompt="p", response_schema=_OverviewModel))

        assert gc.call_count == 3

    async def test_valid_schema_json_no_fallback(self, backend):
        """满足 schema 的合法 JSON 不触发降级（单次调用）。"""
        gc = AsyncMock(return_value=_resp(_VALID_JSON, 20, 10))
        backend._test_client.aio.models.generate_content = gc

        result = await backend.generate(TextGenerationRequest(prompt="p", response_schema=_OverviewModel))

        assert gc.call_count == 1
        assert result.text == _VALID_JSON

    async def test_dict_schema_prose_fallback_returns_stripped_json(self, backend):
        """dict schema 无 Pydantic 模型可校验，降级后按「合法 JSON」标准放行。"""
        schema = {"type": "object", "properties": {"k": {"type": "string"}}}
        gc = AsyncMock(side_effect=[_resp(_PROSE), _resp('```json\n{"k": "v"}\n```')])
        backend._test_client.aio.models.generate_content = gc

        result = await backend.generate(TextGenerationRequest(prompt="p", response_schema=schema))

        assert result.text == '{"k": "v"}'

    async def test_fallback_transient_error_does_not_replay_native_call(self, backend):
        """降级路径瞬态错误只在降级层重试，不重放已成功（已计费）的原生调用。"""
        transient = ConnectionError("503 service unavailable")
        gc = AsyncMock(side_effect=[_resp(_PROSE), transient, transient, transient])
        backend._test_client.aio.models.generate_content = gc

        with patch("lib.retry.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(ConnectionError):
                await backend.generate(TextGenerationRequest(prompt="p", response_schema=_OverviewModel))

        # 1 次原生成功 + 降级层自身 3 次重试穷尽；原生调用未被重放
        assert gc.call_count == 4
