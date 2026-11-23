"""GrokImageBackend — xAI Grok (Aurora) 图片生成后端。"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from lib.backends.backend_runtime import read_reference_images
from lib.backends.grok_shared import create_grok_client, grok_should_retry
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
    download_image_to_path,
    per_image_usage,
)
from lib.backends.providers import PROVIDER_GROK
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "grok-imagine-image"

_MAX_REFERENCE_IMAGES = 5

_SUPPORTED_ASPECT_RATIOS = {
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "2:1",
    "1:2",
    "19.5:9",
    "9:19.5",
    "20:9",
    "9:20",
    "auto",
}


def _validate_aspect_ratio(aspect_ratio: str) -> str:
    """校验 aspect_ratio 是否在 Grok 支持列表中，不支持则 warning 并透传。"""
    if aspect_ratio not in _SUPPORTED_ASPECT_RATIOS:
        logger.warning("Grok 可能不支持 aspect_ratio=%s，将透传给 API", aspect_ratio)
    return aspect_ratio


class GrokImageBackend:
    """xAI Grok (Aurora) 图片生成后端，支持 T2I 和 I2I。"""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = _MAX_REFERENCE_IMAGES

    CHANNEL_DEFAULT_QUALITY: ClassVar[dict[bool, str]] = {False: "low", True: "medium"}

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_grok_client(api_key=api_key)
        self._model = model or DEFAULT_MODEL

    @property
    def name(self) -> str:
        return PROVIDER_GROK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    @classmethod
    def _usage(cls, image_size: str | None, reference_count: int | None) -> Usage:
        dimensions: dict[str, object] = {"quality": cls.CHANNEL_DEFAULT_QUALITY[reference_count != 0]}
        if image_size is not None:
            dimensions["resolution"] = image_size
        return per_image_usage(output_images=1, input_images=reference_count, dimensions=dimensions)

    @classmethod
    def estimate_usage(cls, facts: ImageRequestFacts) -> Usage:
        return cls._usage(facts.image_size, facts.reference_count)

    @with_retry_async(retry_if=grok_should_retry)
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """生成图片（T2I 或 I2I）。"""
        generate_kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "aspect_ratio": _validate_aspect_ratio(request.aspect_ratio),
        }
        if request.image_size is not None:
            generate_kwargs["resolution"] = request.image_size.lower()

        reference_count = 0
        if request.reference_images:
            data_uris = await asyncio.to_thread(
                lambda: [
                    image.data_uri()
                    for image in read_reference_images((ref.path for ref in request.reference_images), self.model)
                ]
            )
            generate_kwargs["image_urls"] = data_uris
            reference_count = len(data_uris)

        logger.info("Grok 图片生成开始: model=%s", self._model)
        logger.info("调用 %s 图片 SDK kwargs=%s", self.name, format_kwargs_for_log(generate_kwargs))
        response = await self._client.image.sample(**generate_kwargs)

        if not response.respect_moderation:
            raise RuntimeError("Grok 图片生成被内容审核拒绝")

        await download_image_to_path(response.url, request.output_path)

        logger.info("Grok 图片下载完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            image_uri=response.url,
            usage=self._usage(request.image_size, reference_count),
        )
