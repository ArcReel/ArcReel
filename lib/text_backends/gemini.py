"""Gemini 文本生成后端。"""

from __future__ import annotations

import logging
from pathlib import Path

try:
    from google import genai
    from PIL import Image
except ImportError:
    genai = None  # type: ignore
    Image = None  # type: ignore

from ..config.url_utils import normalize_base_url
from ..gemini_shared import VERTEX_SCOPES, with_retry_async
from ..providers import PROVIDER_GEMINI
from .base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    resolve_schema,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3-flash-preview"


def _stringify_integer_enums(schema: dict) -> None:
    """递归地将 JSON Schema 中的整数枚举值转为字符串。

    Gemini API 的 structured output 只接受字符串枚举，
    但 Pydantic 的 Literal[4, 6, 8] 会生成整数枚举。
    就地修改传入的 schema dict。
    """
    if isinstance(schema, dict):
        if "enum" in schema and all(isinstance(v, int) for v in schema["enum"]):
            schema["enum"] = [str(v) for v in schema["enum"]]
            schema.pop("type", None)  # 移除 "type": "integer"，让 Gemini 推断为字符串
        for v in schema.values():
            if isinstance(v, dict):
                _stringify_integer_enums(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        _stringify_integer_enums(item)


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

            credentials_file = resolve_vertex_credentials_path(Path(__file__).parent.parent.parent)
            if credentials_file is None:
                raise ValueError("未找到 Vertex AI 凭证文件\n请将服务账号 JSON 文件放入 vertex_keys/ 目录")

            with open(credentials_file) as f:
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
            self._client = genai.Client(api_key=api_key, http_options=http_options)
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
    ) -> dict:
        """构建 generate_content 的 config 字典。"""
        config: dict = {}
        if response_schema:
            config["response_mime_type"] = "application/json"
            if isinstance(response_schema, type):
                # Pydantic type → dict schema，修复整数枚举后以 JSON Schema 传递。
                # Gemini API 不支持整数枚举值（如 Literal[4, 6, 8]），
                # 需转为字符串枚举才能通过验证。
                schema_dict = resolve_schema(response_schema)
                _stringify_integer_enums(schema_dict)
                config["response_json_schema"] = schema_dict
            else:
                config["response_json_schema"] = response_schema
        if system_prompt:
            config["system_instruction"] = system_prompt
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

    @with_retry_async(max_attempts=3, backoff_seconds=(2, 4, 8))
    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        """异步生成文本，支持结构化输出和 vision。"""
        config = self._build_config(request.response_schema, request.system_prompt)
        contents = self._build_contents(request)

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config if config else None,
        )

        text = response.text.strip() if response.text else ""

        input_tokens: int | None = None
        output_tokens: int | None = None
        if response.usage_metadata is not None:
            input_tokens = getattr(response.usage_metadata, "prompt_token_count", None)
            output_tokens = getattr(response.usage_metadata, "candidates_token_count", None)

        return TextGenerationResult(
            text=text,
            provider=PROVIDER_GEMINI,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
