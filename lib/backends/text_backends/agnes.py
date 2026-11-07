"""AgnesTextBackend: Agnes text-generation backend (OpenAI-compatible /v1/chat/completions).

Agnes provides OpenAI-style Chat Completions through apihub. Authentication/base_url normalization reuse
agnes_shared (single Bearer key + tolerant host/{/v1} suffixes); generation directly reuses OpenAITextBackend:
prefer native ``response_format`` json_schema, selectively falling back to
Instructor for incompatible schemas or proxies without real schema enforcement. Construction injects
Agnes authentication/default models/provider billing attribution and removes vision capabilities;
strip outer Markdown JSON fences from native structured results before returning them for parent validation.
"""

from __future__ import annotations

from lib.backends.agnes_shared import agnes_base_url, resolve_agnes_api_key
from lib.backends.providers import PROVIDER_AGNES
from lib.backends.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    strip_json_code_fence,
)
from lib.backends.text_backends.openai import OpenAITextBackend

DEFAULT_MODEL = "agnes-3.0-flash"


class AgnesTextBackend(OpenAITextBackend):
    """Agnes text backend: reuse OpenAITextBackend native + Instructor fallback, replacing authentication/defaults and stripping structured-output code fences."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        # Validate the single Bearer key at construction: missing keys raise locally, matching AgnesImageBackend fail-loud behavior, rather than
        # delaying failure until upstream 401. Normalize base_url to {host}/v1, tolerating host-only or /v1 suffix inputs.
        super().__init__(
            api_key=resolve_agnes_api_key(api_key),
            model=model or DEFAULT_MODEL,
            base_url=agnes_base_url(base_url),
            provider_name=PROVIDER_AGNES,
        )
        # Agnes text models declare text generation and structured output only, without VISION (included by default in the parent).
        self._capabilities = {TextCapability.TEXT_GENERATION, TextCapability.STRUCTURED_OUTPUT}

    async def _generate_native(
        self, request: TextGenerationRequest, messages: list[dict]
    ) -> TextGenerationResult | None:
        result = await super()._generate_native(request, messages)
        if result is not None and request.response_schema:
            result.text = strip_json_code_fence(result.text)
        return result
