"""OpenAIImageBackend — OpenAI 图片生成后端。"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
)
from lib.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.providers import PROVIDER_OPENAI
from lib.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-image-1.5"
_MAX_REFERENCE_IMAGES = 16

_SIZE_MAP: dict[str, str] = {
    "9:16": "1024x1792",
    "16:9": "1792x1024",
    "1:1": "1024x1024",
    "3:4": "1024x1792",
    "4:3": "1792x1024",
}

_QUALITY_MAP: dict[str, str] = {
    "512PX": "low",
    "1K": "medium",
    "2K": "high",
    "4K": "high",
}


class OpenAIImageBackend:
    """OpenAI 图片生成后端，支持 T2I 和 I2I。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    @with_retry_async(retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        if request.reference_images:
            return await self._generate_edit(request)
        return await self._generate_create(request)

    async def _generate_create(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        response = await self._client.images.generate(
            model=self._model,
            prompt=request.prompt,
            size=_SIZE_MAP.get(request.aspect_ratio, "1024x1792"),
            quality=_QUALITY_MAP.get(request.image_size, "medium"),
            response_format="b64_json",
            n=1,
        )
        return self._save_and_return(response, request)

    async def _generate_edit(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        refs = request.reference_images
        if len(refs) > _MAX_REFERENCE_IMAGES:
            logger.warning("参考图数量 %d 超过上限 %d，截断", len(refs), _MAX_REFERENCE_IMAGES)
            refs = refs[:_MAX_REFERENCE_IMAGES]
        image_files = []
        try:
            for ref in refs:
                ref_path = Path(ref.path)
                try:
                    image_files.append(open(ref_path, "rb"))  # noqa: SIM115
                except FileNotFoundError:
                    logger.warning("参考图不存在，跳过: %s", ref_path)
            if not image_files:
                logger.warning("所有参考图均无效，回退到 T2I")
                return await self._generate_create(request)
            response = await self._client.images.edit(
                model=self._model,
                image=image_files,
                prompt=request.prompt,
                response_format="b64_json",
            )
        finally:
            for f in image_files:
                f.close()
        return self._save_and_return(response, request)

    def _save_and_return(self, response, request: ImageGenerationRequest) -> ImageGenerationResult:
        image_bytes = base64.b64decode(response.data[0].b64_json)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(image_bytes)
        logger.info("OpenAI 图片生成完成: %s", request.output_path)
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_OPENAI,
            model=self._model,
            quality=_QUALITY_MAP.get(request.image_size, "medium"),
        )
