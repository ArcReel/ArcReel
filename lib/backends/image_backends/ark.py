"""ArkImageBackend — 火山方舟 Seedream 图片生成后端。"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from lib.backends.ark_shared import create_ark_client
from lib.backends.backend_runtime import read_reference_images
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
    per_image_usage,
    save_image_from_response_item,
)
from lib.backends.providers import PROVIDER_ARK
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

_SEEDREAM_2K_SIZE_MAP: dict[str, str] = {
    "1:1": "2048x2048",
    "4:3": "2304x1728",
    "3:4": "1728x2304",
    "16:9": "2848x1600",
    "9:16": "1600x2848",
    "3:2": "2496x1664",
    "2:3": "1664x2496",
    "21:9": "3136x1344",
}
_SEEDREAM_1K_SIZE_MAP: dict[str, str] = {
    "1:1": "1024x1024",
    "4:3": "1152x864",
    "3:4": "864x1152",
    "16:9": "1280x720",
    "9:16": "720x1280",
    "3:2": "1248x832",
    "2:3": "832x1248",
    "21:9": "1512x648",
}


class ArkImageBackend:
    """Ark (火山方舟) Seedream 图片生成后端。"""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = None

    SIZE_MAP: ClassVar[dict[str, str]] = _SEEDREAM_2K_SIZE_MAP
    DEFAULT_SIZE = "2K"

    DEFAULT_MODEL = "doubao-seedream-5-5-261028"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
    ):
        self._client = create_ark_client(api_key=api_key, base_url=base_url, transport_layer=transport_layer)
        self._model = model or self.DEFAULT_MODEL

    @property
    def name(self) -> str:
        return PROVIDER_ARK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    @classmethod
    def _size(cls, image_size: str | None, aspect_ratio: str) -> str:
        return image_size or cls.SIZE_MAP.get(aspect_ratio, cls.DEFAULT_SIZE)

    @classmethod
    def estimate_usage(cls, facts: ImageRequestFacts) -> Usage:
        return per_image_usage(
            output_images=1,
            input_images=facts.reference_count,
            dimensions={"resolution": cls._size(facts.image_size, facts.aspect_ratio)},
        )

    @with_retry_async()
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """异步生成图片（T2I / I2I）。"""
        kwargs: dict = {
            "model": self._model,
            "prompt": request.prompt,
        }

        kwargs["size"] = self._size(request.image_size, request.aspect_ratio)
        reference_count = 0

        if request.reference_images:
            data_uris = await asyncio.to_thread(
                lambda: [
                    image.data_uri()
                    for image in read_reference_images((ref.path for ref in request.reference_images), self.model)
                ]
            )
            reference_count = len(data_uris)
            kwargs["image"] = data_uris[0] if len(data_uris) == 1 else data_uris

        if request.seed is not None:
            kwargs["seed"] = request.seed

        logger.info("调用 %s 图片 SDK kwargs=%s", self.name, format_kwargs_for_log(kwargs))
        response = await asyncio.to_thread(
            self._client.images.generate,
            **kwargs,
        )

        data = getattr(response, "data", None) or []
        if not data:
            raise RuntimeError(f"Ark 图片生成响应 data 为空 (model={self._model})，可能触发内容安全过滤或上游服务异常")
        await save_image_from_response_item(data[0], request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_ARK,
            model=self._model,
            usage=per_image_usage(
                output_images=len(data), input_images=reference_count, dimensions={"resolution": kwargs["size"]}
            ),
        )


class ArkSeedream3ImageBackend(ArkImageBackend):
    """Seedream 3 的 1K 默认尺寸约定。"""

    SIZE_MAP = _SEEDREAM_1K_SIZE_MAP
    DEFAULT_SIZE = "1K"
    DEFAULT_MODEL = "doubao-seedream-3-0-t2i-250415"


SEEDREAM_BACKENDS = {
    "ark-seedream-3": ArkSeedream3ImageBackend,
    "ark-seedream-4": ArkImageBackend,
}
