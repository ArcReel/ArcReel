"""GrokImageBackend — xAI Grok (Aurora) 图片生成后端。"""

from __future__ import annotations

import logging
from pathlib import Path

from lib.grok_shared import create_grok_client, grok_should_retry
from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    download_image_to_path,
    image_to_base64_data_uri,
)
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_GROK
from lib.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "grok-imagine-image"

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

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_grok_client(api_key=api_key)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @property
    def name(self) -> str:
        return PROVIDER_GROK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

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
        if request.reference_images:
            data_uris = []
            for ref in request.reference_images:
                ref_path = Path(ref.path)
                if ref_path.exists():
                    data_uris.append(image_to_base64_data_uri(ref_path))
            if data_uris:
                generate_kwargs["image_urls"] = data_uris
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
        )
