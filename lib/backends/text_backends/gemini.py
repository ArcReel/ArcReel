"""Gemini text-generation backend."""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from google import genai
from PIL import Image

from lib.config.url_utils import normalize_base_url
from lib.infra.logging_utils import format_kwargs_for_log

from ..gemini_shared import VERTEX_SCOPES, gemini_http_options, with_retry_async
from ..providers import PROVIDER_GEMINI
from ..transport_layer import TransportLayer
from .base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    check_truncation,
    merge_billed_usage,
    prompt_json_fallback,
    resolve_schema,
    structured_fallback_reason,
    truncate_for_log,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3-flash-preview"

# These keyword values map names → subschemas: keys are property/definition names, not schema keywords.
# On recursion, each value is a subschema (keys may happen to be ``const`` and must not be treated as keywords).
_SUBSCHEMA_MAP_KEYS = frozenset({"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"})
# These keyword values are instance data, not subschemas: never recurse into them (any ``const`` there is data).
_INSTANCE_KEYWORDS = frozenset({"const", "enum", "default", "examples"})
# Schema keywords unrecognized by Gemini Developer API ``responseSchema``: strip only in keyword positions.
_UNSUPPORTED_KEYWORDS = frozenset({"additionalProperties"})


def _const_to_enum(node: object, *, in_subschema_map: bool = False) -> object:
    """Normalize schemas into forms expressible through ``responseSchema``.

    Three normalization steps in one position-aware traversal:
    1. Scalar ``const: X`` → ``enum: [X]`` (semantically equivalent). Single-value ``Literal`` renders
       as ``const`` in ``model_json_schema()``, but ``types.Schema`` has no ``const`` field.
    2. ``enum`` containing nonstrings → string enum + ``type: string``. ``types.Schema``
       ``enum`` supports strings only (per proto definitions). Converting integer duration enums ``[4,6,8]`` to ``["4","6","8"]``
       preserves exact-set constrained decoding; mechanical coercion by parser-side ``_duration_literal`` restores int.
    3. Strip schema keyword ``additionalProperties``. Strict models (``extra="forbid"``) include
       ``additionalProperties: false`` at every object level in ``model_json_schema()``. google-genai sends false
       as ``additional_properties`` unchanged; Gemini Developer API ``responseSchema`` does not recognize it,
       immediately returning 400 INVALID_ARGUMENT. Constrained decoding itself does not generate extra-schema properties,
       so stripping loses no constraint; downstream still revalidates with strict Pydantic models.

    ``const`` occurs in three positions requiring different handling (the irreducible minimal state machine for correctness):
    - **Schema keywords**: normalize scalar values only, matching this repository's sole const form, single-value duration Literal;
      strip ``additionalProperties`` only in this position too;
    - **Field names** (keys of mappings under ``_SUBSCHEMA_MAP_KEYS``): current dict keys are names, while values remain subschemas;
      recurse into values as schemas (normalize actual const inside; retain fields named ``additionalProperties``);
    - **Instance data** (values of ``_INSTANCE_KEYWORDS``): preserve unchanged without recursion.
    """
    if isinstance(node, list):
        return [_const_to_enum(item) for item in node]
    if not isinstance(node, dict):
        return node
    if in_subschema_map:
        # Current dict keys are property/definition names; each value is a subschema.
        return {k: _const_to_enum(v) for k, v in node.items()}
    out: dict = {}
    for k, v in node.items():
        if k in _UNSUPPORTED_KEYWORDS:
            continue  # Unrecognized by Gemini responseSchema and irrelevant to constrained decoding.
        if k in _INSTANCE_KEYWORDS:
            out[k] = v  # Values are instance data; preserve unchanged.
        else:
            out[k] = _const_to_enum(v, in_subschema_map=k in _SUBSCHEMA_MAP_KEYS)
    if "const" in out and (out["const"] is None or isinstance(out["const"], (str, int, float, bool))):
        out["enum"] = [out.pop("const")]
    if "enum" in out and isinstance(out["enum"], list) and any(not isinstance(x, str) for x in out["enum"]):
        out["enum"] = [str(x) for x in out["enum"]]
        out["type"] = "string"
    return out


def _to_response_schema(schema: dict | type) -> dict:
    """Convert response_schema into dictionaries consumable by Gemini ``response_schema`` (``types.Schema``).

    Gemini has two structured-output channels: ``response_schema`` (wire ``responseSchema``, OpenAPI subset,
    long established and generally forwarded by proxy gateways for real upstream constrained decoding) and ``response_json_schema`` (wire
    ``responseJsonSchema``, newer and unrecognized by most proxy request parsers, which silently drop it; upstream then
    generates freely in schemaless JSON mode, losing all enum/required constraints, reproduced widely in production). Use the former consistently:
    first inline ``$ref`` through ``resolve_schema``, then normalize ``const`` and nonstring ``enum`` through
    ``_const_to_enum`` into string enums expressible by ``types.Schema``, stripping ``additionalProperties`` too.
    """
    normalized = _const_to_enum(resolve_schema(schema))
    assert isinstance(normalized, dict)  # resolve_schema always returns dict.
    return normalized


class GeminiTextBackend:
    """Gemini text backend supporting AI Studio and Vertex AI modes."""

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
            self._client = genai.Client(
                api_key=api_key, http_options=gemini_http_options(effective_base_url, transport_layer)
            )
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
    ) -> genai.types.GenerateContentConfigDict:
        """Build generate_content config dictionaries."""
        config: genai.types.GenerateContentConfigDict = {}
        if response_schema:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = _to_response_schema(response_schema)
        if system_prompt:
            config["system_instruction"] = system_prompt
        if max_output_tokens is not None:
            config["max_output_tokens"] = max_output_tokens
        return config

    def _build_contents(self, request: TextGenerationRequest) -> list:
        """Build contents lists (image parts + text prompts)."""
        contents: list = []

        if request.images:
            for img_input in request.images:
                if img_input.path is not None:
                    pil_img = Image.open(img_input.path)
                    contents.append(pil_img)
                elif img_input.url is not None:
                    # Pass URL images directly as strings; SDK handles them internally.
                    contents.append(img_input.url)

        contents.append(request.prompt)
        return contents

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        """Generate text asynchronously, supporting structured output and vision.

        No retry decorator here: transient retries occur in :meth:`_generate_native`. Including validation/
        fallback in retry scope would let fallback failures replay successful native calls (multiplying retries).
        """
        native = await self._generate_native(request, structured=bool(request.response_schema))

        if request.response_schema:
            # Revalidate successful HTTP 200 responses against schemas. Proxy gateways may silently ignore
            # response_schema and return prose or invalid JSON; accepting it would leak into downstream Pydantic
            # validation and raise raw ValidationError. Native Gemini requests omit strict, so revalidate with strict=False,
            # tolerating coercible values to avoid unnecessary billed fallback calls for valid provider-accepted responses.
            fallback_reason = structured_fallback_reason(native.text, request.response_schema, strict=False)
            if fallback_reason:
                logger.warning(
                    "原生 response_schema %s，降级到 prompt 注入路径；模型原始输出：%s",
                    fallback_reason,
                    truncate_for_log(native.text),
                )
                # When proxies silently ignore responseSchema, every wire-level channel (including instructor genai integration
                # JSON/TOOLS modes) is equally ignored; only prompt-injected schemas remain. Call _generate_native directly,
                # bypassing this revalidation branch, so each fallback attempt retries only itself without multiplying outer retries.
                result = await prompt_json_fallback(
                    request,
                    partial(self._generate_native, structured=True),
                    provider=PROVIDER_GEMINI,
                    model=self._model,
                )
                # This native 200 call was billed; merge its tokens into fallback results to avoid systematic undercounting.
                merge_billed_usage(result, native)
                return result

        return native

    @with_retry_async()
    async def _generate_native(self, request: TextGenerationRequest, *, structured: bool) -> TextGenerationResult:
        """One native SDK call: build config/contents, send requests, handle truncation, and retry transient errors.

        Callers supply ``structured`` from the original generate() intent, independently of ``request.response_schema``
        itself: :func:`.base.prompt_json_fallback` clears ``response_schema`` and still reuses this method
        (schema has moved into prompts), but it remains a structured-output attempt, requiring hard truncation errors rather than warnings.
        """
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
            config=config if config else None,
        )

        text = response.text.strip() if response.text else ""

        input_tokens: int | None = None
        output_tokens: int | None = None
        cache_read_tokens: int | None = None
        usage = response.usage_metadata
        if usage is not None:
            # prompt_token_count includes cached hits (cached_content_token_count); reasoning tokens bill as output
            # but are absent from candidates_token_count and must be added back.
            input_tokens = usage.prompt_token_count
            cache_read_tokens = usage.cached_content_token_count
            candidates = usage.candidates_token_count
            thoughts = usage.thoughts_token_count
            output_tokens = None if candidates is None and thoughts is None else (candidates or 0) + (thoughts or 0)

        candidates = getattr(response, "candidates", None) or []
        truncated = False
        if candidates:
            finish_reason = getattr(candidates[0], "finish_reason", None)
            # Gemini finish_reason may be enum objects; convert to str before comparing.
            truncated = check_truncation(
                str(finish_reason).rsplit(".", 1)[-1] if finish_reason is not None else None,
                provider=PROVIDER_GEMINI,
                model=self._model,
                output_tokens=output_tokens,
                structured=structured,
            )

        return TextGenerationResult(
            text=text,
            provider=PROVIDER_GEMINI,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            truncated=truncated,
            cache_read_tokens=cache_read_tokens,
        )
