"""Gemini 文本生成后端。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import cast

import httpx
from google import genai
from google.genai import types
from google.genai.errors import ClientError
from PIL import Image

from lib.config.url_utils import normalize_base_url
from lib.infra.async_thread import run_sync_transaction

from ..gemini_shared import VERTEX_SCOPES
from ..providers import PROVIDER_GEMINI
from ..transport_layer import TransportLayer, layer_async_client
from .base import (
    RequestTokens,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    check_truncation,
    resolve_schema,
)
from .streaming import CONNECT_TIMEOUT, NativeSchemaRejected, TextDelta, execute_stream

DEFAULT_MODEL = "gemini-3.8-flash"

_SUBSCHEMA_MAP_KEYS = frozenset({"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"})
_INSTANCE_KEYWORDS = frozenset({"const", "enum", "default", "examples"})
_UNSUPPORTED_KEYWORDS = frozenset({"additionalProperties"})


def _const_to_enum(node: object, *, in_subschema_map: bool = False) -> object:
    """归一 schema 为 ``responseSchema`` 通道可表达的形态。

    三步归一（同一次位置感知遍历完成）：
    1. 「值为标量」的 ``const: X`` → ``enum: [X]``（语义等价）。单值 ``Literal`` 在
       ``model_json_schema()`` 里渲染为 ``const``，而 ``types.Schema`` 无 ``const`` 字段。
    2. 含非字符串成员的 ``enum`` → 字符串枚举 + ``type: string``。``types.Schema`` 的
       ``enum`` 仅支持字符串（proto 定义如此），整数时长枚举 ``[4,6,8]`` 转为 ``["4","6","8"]``
       后精确集合的约束解码依然成立；解析侧 ``_duration_literal`` 的机械强转恢复 int。
    3. 剥除 schema 关键字 ``additionalProperties``。严格模型（``extra="forbid"``）的
       ``model_json_schema()`` 在每层对象都带 ``additionalProperties: false``，google-genai 对假值
       原样发出 ``additional_properties``，Gemini Developer API 的 ``responseSchema`` 不认识该字段，
       直接 400 INVALID_ARGUMENT。约束解码本身不会生成 schema 外的属性，剥除不损失约束，
       下游仍按严格 Pydantic 模型复验。

    ``const`` 出现的位置有三种，须区分对待（这是正确性的不可约最小状态机）：
    - **schema 关键字**：归一（仅标量，对齐本仓库唯一的 const 形态——单值时长 Literal）；
      ``additionalProperties`` 同理仅在此位置剥除；
    - **字段名**（``_SUBSCHEMA_MAP_KEYS`` 映射的 key）：当前 dict 的 key 是名字，其值仍是子 schema，
      继续按 schema 递归（里面真正的 const 照常归一；叫 ``additionalProperties`` 的字段保留）；
    - **实例数据**（``_INSTANCE_KEYWORDS`` 的值）：原样保留、不递归。
    """
    if isinstance(node, list):
        return [_const_to_enum(item) for item in node]
    if not isinstance(node, dict):
        return node
    if in_subschema_map:
        return {k: _const_to_enum(v) for k, v in node.items()}
    out: dict = {}
    for k, v in node.items():
        if k in _UNSUPPORTED_KEYWORDS:
            continue
        if k in _INSTANCE_KEYWORDS:
            out[k] = v
        else:
            out[k] = _const_to_enum(v, in_subschema_map=k in _SUBSCHEMA_MAP_KEYS)
    if "const" in out and (out["const"] is None or isinstance(out["const"], (str, int, float, bool))):
        out["enum"] = [out.pop("const")]
    if "enum" in out and isinstance(out["enum"], list) and any(not isinstance(x, str) for x in out["enum"]):
        out["enum"] = [str(x) for x in out["enum"]]
        out["type"] = "string"
    return out


def _to_response_schema(schema: dict | type) -> dict:
    """把 response_schema 统一转成 Gemini ``response_schema``（``types.Schema``）可消费的 dict。

    Gemini 有两条结构化输出通道：``response_schema``（wire 字段 ``responseSchema``，OpenAPI 子集，
    上线已久，代理网关普遍能透传给真实上游执行约束解码）与 ``response_json_schema``（wire 字段
    ``responseJsonSchema``，较新，多数代理网关的请求解析结构体不认识、会静默丢弃——丢弃后上游按
    无 schema 的纯 JSON 模式自由生成，枚举/必填约束全部失效，线上大面积复现）。统一走前者：
    先 ``resolve_schema`` 内联 ``$ref``，再经 ``_const_to_enum`` 把 ``const`` 与非字符串 ``enum``
    归一为 ``types.Schema`` 可表达的字符串枚举，并剥除 ``additionalProperties``。
    """
    normalized = _const_to_enum(resolve_schema(schema))
    assert isinstance(normalized, dict)
    return normalized


class GeminiTextBackend:
    """Gemini 文本生成后端，支持 AI Studio 和 Vertex AI 两种模式。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        backend: str = "aistudio",
        base_url: str | None = None,
        gcs_bucket: str | None = None,
        credentials_path: str | None = None,
        transport_layer: TransportLayer | None = None,
    ):
        self._model = model or DEFAULT_MODEL
        raw_backend = backend or "aistudio"
        self._backend = str(raw_backend).strip().lower() or "aistudio"

        self._transport_layer = transport_layer
        self._base_url = None
        if self._backend == "vertex":
            import json as json_module

            from google.oauth2 import service_account

            from lib.config.system_config import resolve_vertex_credentials_path

            credentials_file = Path(credentials_path) if credentials_path else resolve_vertex_credentials_path()
            if credentials_file is None:
                raise ValueError("未找到 Vertex AI 凭证文件\n请将服务账号 JSON 文件放入 vertex_keys/ 目录")

            with open(credentials_file, encoding="utf-8") as f:
                creds_data = json_module.load(f)
            project_id = creds_data.get("project_id")

            if not project_id:
                raise ValueError(f"凭证文件 {credentials_file} 中未找到 project_id")

            credentials = service_account.Credentials.from_service_account_file(
                str(credentials_file), scopes=VERTEX_SCOPES
            )

            self._make_client = partial(
                genai.Client,
                vertexai=True,
                project=project_id,
                location="global",
                credentials=credentials,
            )
        else:
            if not api_key:
                raise ValueError("Gemini API Key 未提供（API Key is required for AI Studio mode）。")
            self._base_url = normalize_base_url(base_url)
            self._make_client = partial(genai.Client, api_key=api_key)

    @property
    def name(self) -> str:
        return PROVIDER_GEMINI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    def _build_config(
        self,
        response_schema: dict | type | None,
        system_prompt: str | None,
        max_output_tokens: int | None = None,
    ) -> genai.types.GenerateContentConfigDict:
        """构建 generate_content 的 config 字典。"""
        config: genai.types.GenerateContentConfigDict = {}
        if response_schema:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = _to_response_schema(response_schema)
        if system_prompt:
            config["system_instruction"] = system_prompt
        if max_output_tokens is not None:
            config["max_output_tokens"] = max_output_tokens
        return config

    def _build_contents(self, request: TextGenerationRequest) -> types.UserContent:
        parts = []
        with ExitStack() as images:
            for image in request.images or []:
                if image.path is not None:
                    parts.append(images.enter_context(Image.open(image.path)))
                elif image.url is not None:
                    parts.append(image.url)
            parts.append(request.prompt)
            return types.UserContent(parts)

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        return await execute_stream(
            request, self._stream, provider=self.name, protocol="gemini_generate", model=self.model
        )

    @asynccontextmanager
    async def _session(self) -> AsyncGenerator[genai.client.AsyncClient]:
        timeout = httpx.Timeout(None, connect=CONNECT_TIMEOUT, pool=CONNECT_TIMEOUT)

        async def configure_timeout(wire: httpx.Request) -> None:
            wire.extensions["timeout"] = timeout.as_dict()

        async with AsyncExitStack() as stack:

            async def track_response(response: httpx.Response) -> None:
                stack.push_async_callback(response.aclose)

            def build_http() -> httpx.AsyncClient:
                http = layer_async_client(
                    httpx.AsyncClient(
                        follow_redirects=True,
                        event_hooks={"request": [configure_timeout], "response": [track_response]},
                    ),
                    self._transport_layer,
                )
                stack.push_async_callback(http.aclose)
                return http

            http = await run_sync_transaction(build_http)
            await stack.enter_async_context(http)
            options = types.HttpOptions(
                base_url=self._base_url or None,
                httpx_async_client=http,
                retry_options=types.HttpRetryOptions(attempts=1),
            )

            def open_client() -> genai.Client:
                return stack.enter_context(self._make_client(http_options=options))

            client = await run_sync_transaction(open_client)
            yield await stack.enter_async_context(client.aio)

    async def _stream(self, request: TextGenerationRequest) -> AsyncGenerator[TextDelta | TextGenerationResult]:
        config = self._build_config(request.response_schema, request.system_prompt, request.max_output_tokens)
        contents = await asyncio.to_thread(self._build_contents, request)
        async with self._session() as aio:
            try:
                chunks = cast(
                    AsyncIterator[types.GenerateContentResponse],
                    await aio.models._generate_content_stream(model=self.model, contents=contents, config=config),
                )
            except ClientError as exc:
                if request.response_schema is not None and exc.code in (400, 422):
                    raise NativeSchemaRejected() from exc
                raise
            yield TextDelta("")
            usage = types.GenerateContentResponseUsageMetadata()
            text = []
            delivered = False
            try:
                async for chunk in chunks:
                    if chunk.usage_metadata is not None:
                        usage = usage.model_copy(update=chunk.usage_metadata.model_dump(exclude_none=True))
                    if not chunk.candidates:
                        continue
                    candidate = chunk.candidates[0]
                    for part in (candidate.content.parts or []) if candidate.content else []:
                        if part.text:
                            if not part.thought:
                                text.append(part.text)
                            yield TextDelta(part.text, thinking=bool(part.thought))
                    if (
                        candidate.finish_reason
                        and candidate.finish_reason != types.FinishReason.FINISH_REASON_UNSPECIFIED
                    ):
                        tokens = _tokens(usage)
                        result = TextGenerationResult(
                            text="".join(text).strip(),
                            provider=self.name,
                            model=self.model,
                            truncated=check_truncation(
                                candidate.finish_reason.value,
                                provider=self.name,
                                model=self.model,
                                structured=False,
                                truncation_values=("MAX_TOKENS", "CONTINUATION"),
                                output_tokens=tokens.output_tokens,
                            ),
                            **asdict(tokens),
                        )
                        delivered = True
                        yield result
                        return
            finally:
                if not delivered and request.on_usage:
                    tokens = _tokens(usage)
                    if tokens != RequestTokens():
                        request.on_usage(tokens)


def _tokens(usage: types.GenerateContentResponseUsageMetadata) -> RequestTokens:
    candidates = usage.candidates_token_count
    thoughts = usage.thoughts_token_count
    return RequestTokens(
        input_tokens=usage.prompt_token_count,
        cache_read_tokens=usage.cached_content_token_count,
        output_tokens=None if candidates is None and thoughts is None else (candidates or 0) + (thoughts or 0),
    )
