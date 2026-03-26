"""ArkImageBackend — 火山方舟 Seedream 图片生成后端。"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Optional, Set

from lib.video_backends.base import PROVIDER_ARK
from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
)
from volcenginesdkarkruntime import Ark

logger = logging.getLogger(__name__)


class ArkImageBackend:
    """Ark (火山方舟) Seedream 图片生成后端。"""

    DEFAULT_MODEL = "doubao-seedream-5-0-lite-260128"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._api_key = api_key or os.environ.get("ARK_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Ark API Key 未提供。请在「全局设置 → 供应商」页面配置 API Key。"
            )

        self._client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=self._api_key,
        )
        self._model = model or self.DEFAULT_MODEL
        self._capabilities: Set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return PROVIDER_ARK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> Set[ImageCapability]:
        return self._capabilities

    # ------------------------------------------------------------------
    # 核心生成方法
    # ------------------------------------------------------------------

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """异步生成图片（T2I / I2I）。"""
        # 构建 SDK 参数
        kwargs: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "response_format": "b64_json",
        }

        # I2I: 读取参考图并 base64 编码
        if request.reference_images:
            b64_images = [
                base64.b64encode(Path(ref.path).read_bytes()).decode()
                for ref in request.reference_images
            ]
            # 单张传字符串，多张传列表
            kwargs["image"] = b64_images[0] if len(b64_images) == 1 else b64_images

        if request.seed is not None:
            kwargs["seed"] = request.seed

        # 同步 SDK 通过 to_thread 包装
        response = await asyncio.to_thread(
            self._client.images.generate,
            **kwargs,
        )

        # 解码并保存
        image_data = base64.b64decode(response.data[0].b64_json)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(image_data)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_ARK,
            model=self._model,
        )
