"""ArkImageBackend — 火山方舟 Seedream 图片生成后端。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from lib.ark_shared import create_ark_client
from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    image_to_base64_data_uri,
    save_image_from_response_item,
)
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_ARK
from lib.retry import with_retry_async

logger = logging.getLogger(__name__)

# Seedream `size` 参数：不传时 4.x/5.x 默认 2048x2048（1:1），3.0-t2i 默认 1024x1024（1:1）。
# 不显式传 size 会让项目设置的 aspect_ratio 完全失效。
#
# Ark 官方推荐宽高像素值（方式 2）：
#   - 4.x/5.x 系列 2K 档（总像素须 ≥ 3_686_400）
#   - 3.0-t2i 系列 1K 档（单边 ∈ [512, 2048]）
# 参考：https://www.volcengine.com/docs/82379/1666946
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


def _resolve_seedream_size(model_id: str, aspect_ratio: str) -> str:
    """按模型族选尺寸表；未识别比例时回退到分辨率 keyword（方式 1，由模型按 prompt 自适应）。"""
    mid = (model_id or "").lower()
    if "seedream-3" in mid:
        size = _SEEDREAM_1K_SIZE_MAP.get(aspect_ratio)
        return size or "1K"
    # 默认按 4.x/5.x 处理（含 lite 与未来兼容版本）
    size = _SEEDREAM_2K_SIZE_MAP.get(aspect_ratio)
    return size or "2K"


class ArkImageBackend:
    """Ark (火山方舟) Seedream 图片生成后端。"""

    DEFAULT_MODEL = "doubao-seedream-5-0-lite-260128"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_ark_client(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @property
    def name(self) -> str:
        return PROVIDER_ARK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    @with_retry_async()
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """异步生成图片（T2I / I2I）。"""
        # 构建 SDK 参数
        kwargs: dict = {
            "model": self._model,
            "prompt": request.prompt,
        }

        # Seedream 不显式传 size 时默认输出 1:1（4.x/5.x: 2048x2048；3.0-t2i: 1024x1024），
        # 项目设置的 aspect_ratio 会被静默忽略。优先使用 caller 显式传入的 image_size
        # （如 grid 路径会传 "2K"/"4K"），否则按官方推荐表从 aspect_ratio 推导宽高。
        kwargs["size"] = request.image_size or _resolve_seedream_size(self._model, request.aspect_ratio)

        # I2I: 读取参考图并转为 base64 data URI
        if request.reference_images:
            data_uris = [image_to_base64_data_uri(Path(ref.path)) for ref in request.reference_images]
            # 单张传字符串，多张传列表
            kwargs["image"] = data_uris[0] if len(data_uris) == 1 else data_uris

        if request.seed is not None:
            kwargs["seed"] = request.seed

        logger.info("调用 %s 图片 SDK kwargs=%s", self.name, format_kwargs_for_log(kwargs))
        # 同步 SDK 通过 to_thread 包装
        response = await asyncio.to_thread(
            self._client.images.generate,
            **kwargs,
        )

        data = getattr(response, "data", None) or []
        if not data:
            # 空 data 通常是内容安全过滤命中或上游网关异常，给出清晰错误便于排查
            raise RuntimeError(f"Ark 图片生成响应 data 为空 (model={self._model})，可能触发内容安全过滤或上游服务异常")
        await save_image_from_response_item(data[0], request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_ARK,
            model=self._model,
        )
