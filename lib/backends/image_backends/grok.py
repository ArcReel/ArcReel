"""GrokImageBackend — xAI Grok (Aurora) 图片生成后端。"""

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

# 多图编辑单请求最多 5 张源图（grok-imagine-image / grok-imagine-image-pro 同一上限）。
# 参考：https://docs.x.ai/developers/model-capabilities/images/multi-image-editing
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


def _encode_existing_refs(ref_paths: list[str]) -> list[str]:
    """跳过不存在的参考图，其余编码为 base64 data URI。"""
    return [image_to_base64_data_uri(path) for ref in ref_paths if (path := Path(ref)).exists()]


class GrokImageBackend:
    """xAI Grok (Aurora) 图片生成后端，支持 T2I 和 I2I。"""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES

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

    @property
    def max_reference_images(self) -> int:
        return _MAX_REFERENCE_IMAGES

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        refs = facts.reference_count
        return per_image_usage(
            output_images=1,
            input_images=None if refs is None else min(refs, _MAX_REFERENCE_IMAGES),
            dimensions={"resolution": facts.image_size} if facts.image_size is not None else None,
        )

    @with_retry_async(retry_if=grok_should_retry)
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """生成图片（T2I 或 I2I）。"""
        generate_kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "aspect_ratio": _validate_aspect_ratio(request.aspect_ratio),
        }
        if request.image_size is not None:
            generate_kwargs["resolution"] = request.image_size

        # I2I：将所有参考图转为 base64 data URI 列表
        reference_count = 0
        if request.reference_images:
            refs = request.reference_images
            if len(refs) > _MAX_REFERENCE_IMAGES:
                logger.warning("Grok 参考图数量 %d 超过上限 %d，截断", len(refs), _MAX_REFERENCE_IMAGES)
                refs = refs[:_MAX_REFERENCE_IMAGES]
            # 存在性检查与读图编码都是阻塞 I/O，整体卸载到线程，避免堵住事件循环
            data_uris = await asyncio.to_thread(_encode_existing_refs, [ref.path for ref in refs])
            if data_uris:
                generate_kwargs["image_urls"] = data_uris
                reference_count = len(data_uris)
                logger.info("Grok I2I 模式: %d 张参考图", len(data_uris))

        logger.info("Grok 图片生成开始: model=%s", self._model)
        logger.info("调用 %s 图片 SDK kwargs=%s", self.name, format_kwargs_for_log(generate_kwargs))
        response = await self._client.image.sample(**generate_kwargs)

        # 审核检查
        if not response.respect_moderation:
            raise RuntimeError("Grok 图片生成被内容审核拒绝")

        # 下载图片到本地
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
                dimensions={"resolution": generate_kwargs["resolution"]} if "resolution" in generate_kwargs else None,
            ),
        )
