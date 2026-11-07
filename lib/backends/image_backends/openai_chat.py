"""OpenAIChatImageBackend: Chat Completions image generation with ``image`` in ``modalities``.

Use the same API as text ``openai-chat``: send prompts/references in one multipart user message,
references as ``image_url`` data-URI content. Relay output locations vary: OpenRouter uses
``choices[0].message.images[].image_url.url``; others inline Markdown in ``content``. Accept both,
with images supplied as data URIs or downloadable URLs.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from pathlib import Path
from typing import Any, ClassVar

from openai.types.chat import ChatCompletion, ChatCompletionContentPartImageParam, ChatCompletionContentPartParam

from arcreel_market_core.aspect_size import IMAGE_TIER_SHORT_EDGE
from lib.backends.backend_runtime import with_artifact_retry
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    download_image_to_path,
    image_to_base64_data_uri,
)
from lib.backends.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.backends.providers import PROVIDER_OPENAI
from lib.backends.transport_layer import TransportLayer
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

#: Forward image_config.image_size tier labels only, 1K / 2K / 4K …; custom WxH has no matching value and is omitted.
_IMAGE_SIZE_TIERS = {k.lower(): k for k in IMAGE_TIER_SHORT_EDGE}
#: Inline content Markdown images ``![alt](url)``, accepting data URIs or http(s).
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)\s*\)")


def _image_config(request: ImageGenerationRequest) -> dict[str, str]:
    config = {"aspect_ratio": request.aspect_ratio}
    tier = _IMAGE_SIZE_TIERS.get((request.image_size or "").strip().lower())
    if tier:
        config["image_size"] = tier
    return config


def _extract_image_url(message: Any) -> str | None:
    """First assistant image URL, preferring ``images`` then Markdown inside ``content``."""
    for item in getattr(message, "images", None) or []:
        image_url = item.get("image_url") if isinstance(item, dict) else getattr(item, "image_url", None)
        url = image_url.get("url") if isinstance(image_url, dict) else getattr(image_url, "url", None)
        if url:
            return url
    content = getattr(message, "content", None)
    if isinstance(content, str):
        match = _MARKDOWN_IMAGE.search(content)
        if match:
            return match.group(1)
    return None


async def _save_image(url: str, output_path: Path) -> None:
    if not url.startswith("data:"):
        # Retry retrieval only in downloads, never repeat already billed image requests.
        await with_artifact_retry(lambda: download_image_to_path(url, output_path), label="Chat Completions 图像")
        return

    def _decode_and_save() -> None:
        _, _, payload = url.partition(",")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(base64.b64decode(payload))

    await run_noninterruptible_sync(_decode_and_save)


class OpenAIChatImageBackend:
    """Chat Completions image generation supporting text-to-image and image-to-image."""

    # Protocols wrap multiple model families; channels impose no counts. Use entries, otherwise let upstream decide.
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = None

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
        provider_name: str = PROVIDER_OPENAI,
    ):
        self._client = create_openai_client(api_key=api_key, base_url=base_url, transport_layer=transport_layer)
        self._model = model
        self._provider_name = provider_name

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        content: list[ChatCompletionContentPartParam] = [{"type": "text", "text": request.prompt}]
        content.extend(await asyncio.to_thread(self._reference_parts, request))
        response = await self._complete(content, request)
        message = response.choices[0].message if response.choices else None
        url = _extract_image_url(message) if message is not None else None
        if not url:
            raise RuntimeError(
                f"Chat Completions 出图响应中没有图片 (model={self._model})，可能触发内容安全过滤或模型不支持出图"
            )
        await _save_image(url, request.output_path)
        logger.info("Chat Completions 出图完成: %s", request.output_path)

        # Report existing total-input/total-output fields, summed by readers in usage_repo.
        usage = response.usage
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self._provider_name,
            model=self._model,
            text_input_tokens=usage.prompt_tokens if usage is not None else None,
            image_output_tokens=usage.completion_tokens if usage is not None else None,
        )

    @with_retry_async(retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def _complete(
        self, content: list[ChatCompletionContentPartParam], request: ImageGenerationRequest
    ) -> ChatCompletion:
        return await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": content}],
            modalities=["image", "text"],  # type: ignore[list-item]  # SDK types enumerate text / audio only.
            extra_body={"image_config": _image_config(request)},
        )

    def _reference_parts(self, request: ImageGenerationRequest) -> list[ChatCompletionContentPartImageParam]:
        parts: list[ChatCompletionContentPartImageParam] = []
        for ref in request.reference_images:
            ref_path = Path(ref.path)
            try:
                data_uri = image_to_base64_data_uri(ref_path)
            except FileNotFoundError:
                logger.warning("参考图不存在，跳过: %s", ref_path)
                continue
            parts.append({"type": "image_url", "image_url": {"url": data_uri}})
        if request.reference_images and not parts:
            # User requested I2I without usable materials: error instead of silently downgrading to T2I.
            raise ImageCapabilityError(
                "image_endpoint_mismatch_no_i2i",
                model=self._model,
                detail="all reference images failed to open",
            )
        return parts
