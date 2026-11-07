"""GrokImageBackend: xAI Grok (Aurora) image backend."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import ClassVar

from lib.backends.grok_shared import create_grok_client, grok_should_retry
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
    download_image_to_path,
    image_to_base64_data_uri,
    per_image_usage,
)
from lib.backends.providers import PROVIDER_GROK
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "grok-imagine-image"

# Multireference edits allow at most five source images, shared by grok-imagine-image / grok-imagine-image-pro.
# Reference: https://docs.x.ai/developers/model-capabilities/images/multi-image-editing
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
    """Validate supported Grok aspect ratios, warning but passing through unsupported values."""
    if aspect_ratio not in _SUPPORTED_ASPECT_RATIOS:
        logger.warning("Grok 可能不支持 aspect_ratio=%s，将透传给 API", aspect_ratio)
    return aspect_ratio


def _encode_existing_refs(ref_paths: list[str]) -> list[str]:
    """Skip nonexistent references; encode remaining files as base64 data URIs."""
    return [image_to_base64_data_uri(path) for ref in ref_paths if (path := Path(ref)).exists()]


class GrokImageBackend:
    """xAI Grok (Aurora) image backend supporting T2I and I2I."""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = _MAX_REFERENCE_IMAGES

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

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        refs = facts.reference_count
        return per_image_usage(
            output_images=1,
            input_images=refs,
            dimensions={"resolution": facts.image_size} if facts.image_size is not None else None,
        )

    @with_retry_async(retry_if=grok_should_retry)
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """Generate images through T2I or I2I."""
        generate_kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "aspect_ratio": _validate_aspect_ratio(request.aspect_ratio),
        }
        if request.image_size is not None:
            # xai-sdk convert_image_resolution_to_pb accepts lowercase 1k/2k only; catalog values are 1K/2K.
            generate_kwargs["resolution"] = request.image_size.lower()

        # I2I: convert all references to base64 data URI lists.
        reference_count = 0
        if request.reference_images:
            refs = request.reference_images
            # Existence checks/encoding block I/O; offload together to a thread, keeping event loops responsive.
            data_uris = await asyncio.to_thread(_encode_existing_refs, [ref.path for ref in refs])
            if data_uris:
                generate_kwargs["image_urls"] = data_uris
                reference_count = len(data_uris)
                logger.info("Grok I2I 模式: %d 张参考图", len(data_uris))

        logger.info("Grok 图片生成开始: model=%s", self._model)
        logger.info("调用 %s 图片 SDK kwargs=%s", self.name, format_kwargs_for_log(generate_kwargs))
        response = await self._client.image.sample(**generate_kwargs)

        # Moderation checks.
        if not response.respect_moderation:
            raise RuntimeError("Grok 图片生成被内容审核拒绝")

        # Download images locally.
        await download_image_to_path(response.url, request.output_path)

        logger.info("Grok 图片下载完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            image_uri=response.url,
            usage=per_image_usage(
                output_images=1,
                input_images=reference_count,
                dimensions={"resolution": request.image_size} if request.image_size is not None else None,
            ),
        )
