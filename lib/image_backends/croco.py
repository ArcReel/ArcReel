"""CrocoImageBackend — Croco GPU 图像生成后端（ERNIE Image Turbo）。

走 Croco 统一任务协议：POST /api/v2/jobs（image.generate）→ 轮询到终态 → 下载 image 产物。
ERNIE Image Turbo 只接受纯文本（不接受输入素材），尺寸限定为 7 个固定组合。
ImageBackend 协议是同步的，故 generate() 内部用 CrocoClient.wait_until_terminal 同步封装异步任务。
"""

from __future__ import annotations

import logging

from lib.aspect_size import parse_aspect_ratio
from lib.croco_shared import CrocoClient
from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
)
from lib.providers import PROVIDER_CROCO

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "ernie-image-turbo"

# ERNIE Image Turbo 允许的 7 个尺寸组合（见 Croco 中枢 API 文档）。
# 比例键为约简后的 "宽:高"。
_CROCO_IMAGE_DIMENSIONS: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "2:3": (848, 1264),
    "3:2": (1264, 848),
    "9:16": (768, 1376),
    "16:9": (1376, 768),
    "3:4": (896, 1200),
    "4:3": (1200, 896),
}

_OPERATION = "image.generate"
_CONTRACT_VERSION = "1"


class CrocoImageBackend:
    """Croco 图像后端（统一任务协议，异步任务同步封装）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120.0,
    ) -> None:
        self._client = CrocoClient(token=api_key, base_url=base_url, http_timeout=http_timeout)
        self._model = model or DEFAULT_MODEL

    @property
    def name(self) -> str:
        return PROVIDER_CROCO

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE}

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        width, height = self._resolve_dimensions(request)

        parameters: dict = {
            "prompt": request.prompt,
            "width": width,
            "height": height,
        }
        if request.seed is not None:
            parameters["seed"] = request.seed

        job = await self._client.submit_job(
            model_id=self._model,
            operation=_OPERATION,
            contract_version=_CONTRACT_VERSION,
            parameters=parameters,
        )
        job_id = job["job_id"]

        terminal = await self._client.wait_until_terminal(job_id)
        if terminal.get("status") != "succeeded":
            raise RuntimeError(f"Croco 图像任务未成功: status={terminal.get('status')} error={terminal.get('error')}")

        outputs = await self._client.list_outputs(job_id)
        image_uri = None
        for item in outputs.get("items", []):
            if item.get("output_id") == "image" and item.get("delivery_state") == "ready":
                image_uri = item.get("content_url")
                break
        if image_uri is None:
            raise RuntimeError("Croco 图像任务缺少 image 产物")

        await self._client.download_output(job_id, "image", request.output_path)
        logger.info("Croco 图片生成完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_CROCO,
            model=self._model,
            image_uri=image_uri,
            seed=request.seed,
        )

    def _resolve_dimensions(self, request: ImageGenerationRequest) -> tuple[int, int]:
        """按约简比例查固定尺寸档位；比例不在档位表时回退 1:1。"""
        aw, ah = parse_aspect_ratio(request.aspect_ratio)
        key = f"{aw}:{ah}"
        if key in _CROCO_IMAGE_DIMENSIONS:
            return _CROCO_IMAGE_DIMENSIONS[key]
        logger.warning("Croco 图像无 %s 档位，回退 1:1", key)
        return _CROCO_IMAGE_DIMENSIONS["1:1"]
