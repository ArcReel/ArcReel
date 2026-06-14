"""Gemini 文本生成后端。"""

from __future__ import annotations

import logging

from google import genai
from PIL import Image

from ..config.url_utils import normalize_base_url
from ..gemini_shared import VERTEX_SCOPES, with_retry_async
from ..logging_utils import format_kwargs_for_log
from ..providers import PROVIDER_GEMINI
from .base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    resolve_schema,
    warn_if_truncated,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3-flash-preview"


# 这些关键字的值是「名字 → 子 schema 的映射」：其 key 是属性名/定义名，不是 schema 关键字。
# 递归进入时，每个值才是子 schema（key 可能恰好叫 ``const``，不可当关键字识别）。
_SUBSCHEMA_MAP_KEYS = frozenset({"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"})
# 这些关键字的值是「实例数据」而非子 schema：不可递归进去（里面恰好叫 ``const`` 的内容是数据）。
_INSTANCE_KEYWORDS = frozenset({"const", "enum", "default", "examples"})


def _const_to_enum(node: object, *, in_subschema_map: bool = False) -> object:
    """把 schema 里「值为标量」的 ``const: X`` 归一为 ``enum: [X]``（语义等价）。

    单值 ``Literal`` 在 ``model_json_schema()`` 里渲染为 ``const``，而 ``const`` 不在 Gemini
    ``response_json_schema`` 的受支持特性内（``enum`` 在）。归一后单值约束落到受支持的 ``enum``，
    保留生成层硬约束。

    ``const`` 出现的位置有三种，须区分对待（这是正确性的不可约最小状态机）：
    - **schema 关键字**：归一（仅标量，对齐本仓库唯一的 const 形态——单值时长 Literal）；
    - **字段名**（``_SUBSCHEMA_MAP_KEYS`` 映射的 key）：当前 dict 的 key 是名字，其值仍是子 schema，
      继续按 schema 递归（里面真正的 const 照常归一）；
    - **实例数据**（``_INSTANCE_KEYWORDS`` 的值）：原样保留、不递归。
    """
    if isinstance(node, list):
        return [_const_to_enum(item) for item in node]
    if not isinstance(node, dict):
        return node
    if in_subschema_map:
        # 当前 dict 的 key 是属性名/定义名；每个值才是子 schema
        return {k: _const_to_enum(v) for k, v in node.items()}
    out: dict = {}
    for k, v in node.items():
        if k in _INSTANCE_KEYWORDS:
            out[k] = v  # 值是实例数据，原样保留
        else:
            out[k] = _const_to_enum(v, in_subschema_map=k in _SUBSCHEMA_MAP_KEYS)
    if "const" in out and (out["const"] is None or isinstance(out["const"], (str, int, float, bool))):
        out["enum"] = [out.pop("const")]
    return out


def _to_response_json_schema(schema: dict | type) -> dict:
    """把 response_schema 统一转成 Gemini ``response_json_schema`` 可消费的 JSON Schema dict。

    Gemini 有两条结构化输出通道：``response_schema``（``types.Schema``，OpenAPI 子集，``enum``
    仅支持字符串）与 ``response_json_schema``（标准 JSON Schema，``enum`` 支持字符串与数字）。
    ``build_episode_script_model`` 把 ``duration_seconds`` 收紧为 ``Literal[*supported_durations]``
    的整数 enum，走前者会在 SDK schema 转换时抛 "Input should be a valid string"，故统一走后者：
    先 ``resolve_schema`` 内联 ``$ref``，再把单值 ``const`` 归一为 ``enum``。
    """
    normalized = _const_to_enum(resolve_schema(schema))
    assert isinstance(normalized, dict)  # resolve_schema 必返回 dict
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
    ):
        self._model = model or DEFAULT_MODEL
        raw_backend = backend or "aistudio"
        self._backend = str(raw_backend).strip().lower() or "aistudio"

        if self._backend == "vertex":
            import json as json_module

            from google.oauth2 import service_account

            from ..system_config import resolve_vertex_credentials_path

            credentials_file = resolve_vertex_credentials_path()
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

            self._client = genai.Client(
                vertexai=True,
                project=project_id,
                location="global",
                credentials=credentials,
            )
            logger.info("GeminiTextBackend: 使用 Vertex AI 后端（凭证: %s）", credentials_file.name)
        else:
            if not api_key:
                raise ValueError("Gemini API Key 未提供（API Key is required for AI Studio mode）。")
            effective_base_url = normalize_base_url(base_url)
            http_options = {"base_url": effective_base_url} if effective_base_url else None
            self._client = genai.Client(api_key=api_key, http_options=http_options)  # type: ignore[arg-type]
            if base_url:
                logger.info("GeminiTextBackend: 使用 AI Studio 后端（Base URL: %s）", base_url)
            else:
                logger.info("GeminiTextBackend: 使用 AI Studio 后端")

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
    ) -> dict:
        """构建 generate_content 的 config 字典。"""
        config: dict = {}
        if response_schema:
            config["response_mime_type"] = "application/json"
            config["response_json_schema"] = _to_response_json_schema(response_schema)
        if system_prompt:
            config["system_instruction"] = system_prompt
        if max_output_tokens is not None:
            config["max_output_tokens"] = max_output_tokens
        return config

    def _build_contents(self, request: TextGenerationRequest) -> list:
        """构建 contents 列表（图片 parts + 文本 prompt）。"""
        contents: list = []

        if request.images:
            for img_input in request.images:
                if img_input.path is not None:
                    pil_img = Image.open(img_input.path)
                    contents.append(pil_img)
                elif img_input.url is not None:
                    # URL 型图片直接作为字符串传递，SDK 内部会处理
                    contents.append(img_input.url)

        contents.append(request.prompt)
        return contents

    @with_retry_async()
    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        """异步生成文本，支持结构化输出和 vision。"""
        config = self._build_config(
            request.response_schema,
            request.system_prompt,
            request.max_output_tokens,
        )
        contents = self._build_contents(request)

        logger.info(
            "调用 %s 文本 SDK payload=%s",
            self.name,
            format_kwargs_for_log({"model": self._model, "contents": contents, "config": config or None}),
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config if config else None,  # type: ignore[arg-type]
        )

        text = response.text.strip() if response.text else ""

        input_tokens: int | None = None
        output_tokens: int | None = None
        if response.usage_metadata is not None:
            input_tokens = getattr(response.usage_metadata, "prompt_token_count", None)
            output_tokens = getattr(response.usage_metadata, "candidates_token_count", None)

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish_reason = getattr(candidates[0], "finish_reason", None)
            # Gemini finish_reason 可能是枚举对象，转 str 后再比对
            warn_if_truncated(
                str(finish_reason).rsplit(".", 1)[-1] if finish_reason is not None else None,
                provider=PROVIDER_GEMINI,
                model=self._model,
                output_tokens=output_tokens,
            )

        return TextGenerationResult(
            text=text,
            provider=PROVIDER_GEMINI,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
