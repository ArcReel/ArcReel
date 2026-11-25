"""GeminiTextBackend tests."""

import asyncio
import json

import httpx
import pytest
from google.genai.errors import ClientError, ServerError
from google.oauth2.credentials import Credentials
from pydantic import BaseModel

from lib.backends.text_backends.base import (
    ImageInput,
    RequestTokens,
    TextCapability,
    TextGenerationRequest,
    TextOutputTruncatedError,
)
from lib.backends.text_backends.gemini import GeminiTextBackend
from lib.backends.text_backends.streaming import TextWaitTimeout
from tests.fakes import bounded_poll_clock, captured_vertex_credentials
from tests.http_capture import PathPrefixRewrite, capture_http, request_json
from tests.unit.lib.backends.text_backends.response_schema_support import (
    response_schema_models,
    response_schema_params,
)


def _sse(*frames):
    return httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        content="".join("data: " + json.dumps(frame) + "\n\n" for frame in frames),
    )


def _frame(text, finish=None, *, thinking=False, usage=None):
    candidate = {"content": {"parts": [{"text": text, "thought": thinking}]}}
    if finish:
        candidate["finishReason"] = finish
    frame = {"candidates": [candidate]}
    if usage:
        frame["usageMetadata"] = usage
    return frame


@pytest.fixture
def backend():
    return GeminiTextBackend(api_key="k")


@pytest.fixture(params=["aistudio", "vertex"])
def channel(request, tmp_path):
    layer = PathPrefixRewrite("/", "/")
    if request.param == "aistudio":
        return GeminiTextBackend(api_key="k", transport_layer=layer), layer
    creds = tmp_path / "vertex.json"
    creds.write_text('{"project_id":"vertex-project"}')
    with captured_vertex_credentials() as load:
        load.return_value = Credentials(token="vertex-token")
        return GeminiTextBackend(backend="vertex", credentials_path=str(creds), transport_layer=layer), layer


class Answer(BaseModel):
    answer: int


TOKENS = {"promptTokenCount": 100, "cachedContentTokenCount": 70, "candidatesTokenCount": 5, "thoughtsTokenCount": 3}
BILL = RequestTokens(input_tokens=100, cache_read_tokens=70, output_tokens=8)


@pytest.mark.parametrize(
    ("finish", "usage"),
    [("STOP", TOKENS), ("STOP", None), ("MAX_TOKENS", TOKENS), ("CONTINUATION", TOKENS), (None, TOKENS)],
)
async def test_stream_outcome(channel, finish, usage):
    backend, layer = channel
    bills, progress = [], []
    frames = [_frame("思考", thinking=True) for _ in range(5)]
    frames += [_frame('{"answer":1}', usage=usage), _frame("", finish)]
    request = TextGenerationRequest(
        prompt="question",
        response_schema=Answer,
        on_progress=progress.append,
        on_usage=bills.append,
        system_prompt="system",
    )
    with capture_http() as router, bounded_poll_clock(step=45):
        route = router.post(path__regex=":streamGenerateContent$").mock(return_value=_sse(*frames))
        if finish in {"MAX_TOKENS", "CONTINUATION"}:
            with pytest.raises(TextOutputTruncatedError):
                await backend.generate(request)
        elif finish is None:
            with pytest.raises(RuntimeError, match="terminal"):
                await backend.generate(request)
        else:
            result = await backend.generate(request)
            assert result.text == '{"answer":1}'
            assert result.request_tokens() == ([BILL] if usage else [])
    assert bills == ([BILL] if usage else [])
    assert [(p.characters, p.thinking) for p in progress if p.phase == "receiving"] == [(0, True)] * 5 + [(12, False)]
    assert route.call_count == layer.closed == 1
    wire = route.calls.last.request
    assert wire.url.params["alt"] == "sse"
    assert wire.extensions["timeout"] == {"connect": 15, "read": None, "write": None, "pool": 15}
    body = request_json(wire)
    config = body["generationConfig"]
    assert "responseSchema" in config
    assert "responseJsonSchema" not in config
    assert "maxOutputTokens" not in config
    assert body["systemInstruction"]["parts"][0]["text"] == "system"


async def test_cancel_after_partial_usage(channel):
    backend, layer = channel
    bills = []
    waiting = asyncio.Event()

    class Paused(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _sse(_frame("partial", usage=TOKENS)).content
            waiting.set()
            await asyncio.Event().wait()

    with capture_http() as router:
        route = router.post(path__regex=":streamGenerateContent$").mock(
            return_value=httpx.Response(200, stream=Paused())
        )
        task = asyncio.create_task(backend.generate(TextGenerationRequest(prompt="hello", on_usage=bills.append)))
        try:
            async with asyncio.timeout(5):
                await waiting.wait()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert bills == [BILL]
    assert route.call_count == layer.closed == 1


async def test_native_and_fallback_are_individually_billed_and_redacted(backend, caplog):
    bills = []
    with capture_http() as router, caplog.at_level("INFO"):
        route = router.post(path__regex=":streamGenerateContent$").mock(
            side_effect=[
                _sse(_frame("private output", "STOP", usage=TOKENS)),
                _sse(_frame('<think>abc</think>\n```json\n{"answer":1}\n```', "STOP", usage=TOKENS)),
            ]
        )
        result = await backend.generate(
            TextGenerationRequest(
                prompt="private prompt", max_output_tokens=123, response_schema=Answer, on_usage=bills.append
            )
        )
    assert result.text == '{"answer":1}'
    assert bills == result.request_tokens() == [BILL, BILL]
    first, second = [request_json(call.request) for call in route.calls]
    assert first["generationConfig"]["maxOutputTokens"] == 123
    assert "responseSchema" not in second["generationConfig"]
    assert "JSON Schema" in second["contents"][0]["parts"][0]["text"]
    assert "private prompt" not in caplog.text
    assert "private output" not in caplog.text


async def test_retry_limit_counts_actual_http_requests(channel):
    backend, layer = channel
    with capture_http() as router, bounded_poll_clock(step=1):
        route = router.post(path__regex=":streamGenerateContent$").respond(503, json={"error": {"code": 503}})
        with pytest.raises(ServerError):
            await backend.generate(TextGenerationRequest(prompt="hello"))
    assert route.call_count == layer.closed == 3


def test_defaults_and_capabilities(backend):
    assert (backend.name, backend.model) == ("gemini", "gemini-3.8-flash")
    assert backend.capabilities == {
        TextCapability.TEXT_GENERATION,
        TextCapability.STRUCTURED_OUTPUT,
        TextCapability.VISION,
    }
    with pytest.raises(ValueError, match="API Key"):
        GeminiTextBackend()


class TestResponseSchema:
    def test_episode_script_integer_enum_stringified(self, backend):
        """回归：duration_seconds 整数 enum 在 responseSchema 通道转为字符串枚举。

        types.Schema 的 enum 仅支持字符串，整数枚举原样发出会在真实 SDK 转换时抛
        "Input should be a valid string"。转为字符串枚举后精确集合的约束解码依然成立，
        解析侧 _duration_literal 的机械强转恢复 int。
        """
        from lib.script.script_models import build_episode_script_model

        config = backend._build_config(build_episode_script_model("narration", [4, 6, 8]), None)

        assert "response_json_schema" not in config
        seg_props = config["response_schema"]["properties"]["segments"]["items"]["properties"]
        assert seg_props["duration_seconds"]["enum"] == ["4", "6", "8"]
        assert seg_props["duration_seconds"]["type"] == "string"

    def test_single_value_duration_const_normalized_to_enum(self, backend):
        """单值 supported_durations 渲染为 const（types.Schema 无此字段），
        归一为单元素字符串 enum 以保留生成层硬约束。"""
        from lib.script.script_models import build_episode_script_model

        config = backend._build_config(build_episode_script_model("narration", [8]), None)
        ds = config["response_schema"]["properties"]["segments"]["items"]["properties"]["duration_seconds"]
        assert "const" not in ds
        assert ds["enum"] == ["8"]
        assert ds["type"] == "string"

    def test_const_to_enum_distinguishes_keyword_field_name_and_data(self, backend):
        """区分 const 出现的三种位置：schema 关键字（归一）、字段名（值仍是子 schema）、实例数据（不动）。

        本仓库 const 只来自单值时长 Literal（标量）。位置感知确保：properties 等映射的 key 是字段名，
        其值仍是子 schema（里面真正的 const 照常归一）；const/default 等关键字的值是数据，不递归。
        """
        schema = {
            "type": "object",
            "properties": {
                "duration_seconds": {"const": 8, "type": "integer"},
                "const": {"type": "string"},
                "default": {"const": 6, "type": "integer"},
                "with_default": {"type": "object", "default": {"const": 42}},
                "obj_const": {"const": {"const": 5}},
            },
        }
        props = backend._build_config(schema, None)["response_schema"]["properties"]
        assert props["duration_seconds"] == {"type": "string", "enum": ["8"]}
        assert props["const"] == {"type": "string"}
        assert props["default"] == {"type": "string", "enum": ["6"]}
        assert props["with_default"]["default"] == {"const": 42}
        assert props["obj_const"] == {"const": {"const": 5}}

    def test_all_script_schemas_accepted_by_google_genai_schema(self, backend):
        """集成回归：全部剧本 schema 工厂经 _build_config 产出后必须被真实 types.Schema 接受。

        mock 掉 generate_content 会让 SDK 的 schema 转换不执行，掩盖整数 enum / const 与
        types.Schema 的不兼容（"Input should be a valid string" 在请求发出前即抛，整集生成
        直接失败）。这里直接过真实 SDK 类型校验，堵住该盲区。
        """
        from google.genai import types as gtypes

        from lib.script.script_models import build_episode_script_model

        schemas = response_schema_models() + [
            build_episode_script_model(mode, durations)
            for mode in ("narration", "drama", "ad")
            for durations in ([4, 6, 8], [8])
        ]
        assert schemas, "schema 清单为空时下面的循环形同虚设"
        for schema in schemas:
            config = backend._build_config(schema, None)
            validated = gtypes.Schema.model_validate(config["response_schema"])
            assert validated.type == gtypes.Type.OBJECT


def _keys_anywhere(node: object, names: set[str]) -> list[str]:
    """递归收集 dict 中任何层级出现的指定 key（含属性名位置，用于断言「完全不含」）。"""
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
    """Gemini Developer API 的 responseSchema 不认识 additionalProperties，下发即 400 INVALID_ARGUMENT。"""

    def test_response_schema_models_carry_additional_properties_before_conversion(self):
        """前提自检：原始 schema 确实带 additionalProperties，否则下面的参数化断言形同虚设。"""
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
        """只剥 schema 关键字：同名字段、enum/default/const 实例数据里的同名内容保持原样。"""
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

    async def test_all_models_send_normalized_schema_on_the_wire(self, backend):
        with capture_http() as router:
            route = router.post(path__regex=":streamGenerateContent$").respond(401, json={"error": {"code": 401}})
            for model in response_schema_models():
                with pytest.raises(ClientError):
                    await backend.generate(TextGenerationRequest(prompt="x", response_schema=model))
                schema = request_json(route.calls.last.request)["generationConfig"]["responseSchema"]
                assert schema["type"] == "OBJECT"
                assert _keys_anywhere(schema, {"additionalProperties", "additional_properties"}) == [], model.__name__


async def test_error_in_accepted_stream_does_not_replay(channel):
    backend, layer = channel
    with capture_http() as router, bounded_poll_clock(step=1):
        route = router.post(path__regex=":streamGenerateContent$").mock(return_value=_sse({"error": {"code": 503}}))
        with pytest.raises(ServerError):
            await backend.generate(TextGenerationRequest(prompt="x"))
    assert route.call_count == layer.closed == 1


async def test_native_rejection_falls_back(channel):
    backend, layer = channel
    with capture_http() as router:
        route = router.post(path__regex=":streamGenerateContent$").mock(
            side_effect=[httpx.Response(400, json={"error": {"code": 400}}), _sse(_frame('{"answer":1}', "STOP"))]
        )
        result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Answer))
    assert result.text == '{"answer":1}'
    assert route.call_count == layer.closed == 2


async def test_read_timeout_bills_latest_partial_usage(channel):
    backend, layer = channel
    bills = []

    class TimedOut(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _sse(_frame("partial", usage=TOKENS)).content
            yield _sse({"usageMetadata": {"thoughtsTokenCount": 7}}).content
            raise httpx.ReadTimeout("stalled")

    with capture_http() as router:
        route = router.post(path__regex=":streamGenerateContent$").mock(
            return_value=httpx.Response(200, stream=TimedOut())
        )
        with pytest.raises(TextWaitTimeout):
            await backend.generate(TextGenerationRequest(prompt="x", on_usage=bills.append))
    assert route.call_count == layer.closed == 1
    assert bills == [RequestTokens(input_tokens=100, cache_read_tokens=70, output_tokens=12)]


async def test_image_and_prompt_are_sent_together(backend, tmp_path):
    from PIL import Image

    path = tmp_path / "reference.png"
    Image.new("RGB", (2, 2)).save(path)
    with capture_http() as router:
        route = router.post(path__regex=":streamGenerateContent$").mock(return_value=_sse(_frame("cut", "MAX_TOKENS")))
        result = await backend.generate(TextGenerationRequest(prompt="describe", images=[ImageInput(path=path)]))
    assert result.text == "cut"
    assert result.truncated
    parts = request_json(route.calls.last.request)["contents"][0]["parts"]
    assert parts[0]["inlineData"]["mime_type"] == "image/png"
    assert parts[1] == {"text": "describe"}
