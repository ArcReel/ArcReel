"""GrokImageBackend — xAI Grok (Aurora) 图片生成后端。"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.grok_shared import create_grok_client
from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    image_to_base64_data_uri,
)
from lib.providers import PROVIDER_GROK

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "grok-imagine-image"

_SUPPORTED_ASPECT_RATIOS = {"16:9", "9:16", "4:3", "3:4"}

_ASPECT_RATIO_FALLBACK: dict[str, str] = {
    "1:1": "4:3",
    "3:2": "16:9",
    "2:3": "9:16",
}


def _validate_aspect_ratio(aspect_ratio: str) -> str:
    """校验 aspect_ratio 是否在 Grok 支持列表中，不支持则映射到最近值。"""
    if aspect_ratio in _SUPPORTED_ASPECT_RATIOS:
        return aspect_ratio
    fallback = _ASPECT_RATIO_FALLBACK.get(aspect_ratio)
    if fallback:
        logger.warning("Grok 不支持 aspect_ratio=%s，映射为 %s", aspect_ratio, fallback)
        return fallback
    logger.warning("Grok 不支持 aspect_ratio=%s，回退为 16:9", aspect_ratio)
    return "16:9"


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

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """生成图片（T2I 或 I2I）。"""
        generate_kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "aspect_ratio": _validate_aspect_ratio(request.aspect_ratio),
            "resolution": _map_image_size_to_resolution(request.image_size),
        }

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
        response = await self._client.image.sample(**generate_kwargs)

        # 审核检查
        if not response.respect_moderation:
            raise RuntimeError("Grok 图片生成被内容审核拒绝")

        # 下载图片到本地
        await _download_image(response.url, request.output_path)

        logger.info("Grok 图片下载完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            image_uri=response.url,
        )


def _map_image_size_to_resolution(image_size: str) -> str:
    """将通用 image_size（如 '1K', '2K'）映射为 Grok resolution 参数。"""
    mapping = {
        "1K": "1k",
        "2K": "2k",
        "1k": "1k",
        "2k": "2k",
    }
    return mapping.get(image_size, "1k")


async def _download_image(url: str, output_path: Path, *, timeout: int = 60) -> None:
    """从 URL 下载图片到本地文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient() as http_client:
        resp = await http_client.get(url, timeout=timeout)
        resp.raise_for_status()
        output_path.write_bytes(resp.content)
