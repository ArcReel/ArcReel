"""KlingImageBackend — 可灵 Kling 图像生成后端（JWT 直连 / Bearer 中转双模式，异步轮询）。

走可灵原生图像端点：submit ``POST /v1/images/generations`` 取 ``data.task_id`` →
轮询 ``GET /v1/images/generations/{task_id}`` 至 ``task_status=succeed`` 取
``task_result.images[0].url``（24h 有效）→ 失效前立即下载本地。

鉴权 / base_url 装配 / submit-poll 骨架由 ``KlingBackendBase`` 共享（与 ``KlingVideoBackend`` 同源），
本类只填图像侧差异：端点路径、payload 构建、参考图编码、静态 capability 与 ``api_model_name`` 解耦。

双模式（对齐 ``KlingVideoBackend`` 的 ``auth_mode`` 先例）：
- ``auth_mode="jwt"``（内置 provider）：接 access_key + secret_key，走 ``KlingJWTManager``，
  每次 HTTP 调用前检查过期、距过期 <60s 按需重签——异步渲染可能超单 token 寿命。
- ``auth_mode="bearer"``（自定义 endpoint）：接静态 api_key + base_url，旁路 JWT 管理器。

注册图像模型：
- ``kling-image-o1``（默认）：文生图 / 图生图 / 1-10 图参考（跨图角色一致性），按 ``image`` 数组下传。
- ``kling-v3-omni-image``：文生图 / 图生图 / 组图生成（registry 别名键，API 模型名 ``kling-v3-omni``）。

发给 API 的模型名取 ``api_model_name``（registry 解耦键名与 API 名，供两栖模型共用 API 名），缺省回退
到 registry 键名——不硬发键名，否则别名键会把 ``kling-v3-omni-image`` 误当 API 模型名。

resolution 由模型目录声明可选档位，请求按 API 要求转为小写，计费维度使用大写档位。
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import read_reference_images
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
    ReferenceImage,
    download_image_to_path,
    per_image_usage,
)
from lib.backends.input_limits import excess_image_names
from lib.backends.kling_backend_base import KlingBackendBase
from lib.backends.kling_shared import (
    extract_kling_image_urls,
)
from lib.backends.providers import PROVIDER_KLING
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kling-image-o1"

_IMAGE_ENDPOINT = "images/generations"
_POLL_INTERVAL_SECONDS = 5.0
_POLL_MAX_WAIT_SECONDS = 600.0


class KlingImageBackend(KlingBackendBase):
    """可灵 Kling 图像后端（异步轮询，JWT / Bearer 双模式）。"""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = None

    _media_label = "图像"

    def __init__(
        self,
        *,
        auth_mode: str = "jwt",
        access_key: str | None = None,
        secret_key: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        api_model_name: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        super().__init__(
            auth_mode=auth_mode,
            access_key=access_key,
            secret_key=secret_key,
            api_key=api_key,
            model=model or DEFAULT_MODEL,
            base_url=base_url,
            http_timeout=http_timeout,
            transport_layer=transport_layer,
        )
        self._api_model_name = api_model_name or self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    def _build_payload(self, request: ImageGenerationRequest) -> dict:
        """构建图像请求体。无参考图 → 文生图；有参考图 → 图生图（image 数组）。"""
        payload: dict = {
            "model_name": self._api_model_name,
            "prompt": request.prompt,
            "aspect_ratio": request.aspect_ratio,
            "n": 1,
        }
        if request.image_size:
            payload["resolution"] = request.image_size.lower()
        images = self._encode_references(request.reference_images)
        if images:
            payload["image"] = images
        return payload

    def _encode_references(self, reference_images: list[ReferenceImage]) -> list[str]:
        """参考图 → 纯 base64 列表（无 data URI 前缀）；缺失/不可读 fail-loud。"""
        return [image.base64() for image in read_reference_images((ref.path for ref in reference_images), self.model)]

    @staticmethod
    def _safe_log_view(payload: dict) -> dict:
        """预脱敏标量视图，直接喂 logger（避开 format_kwargs_for_log sink）。

        base64 参考图 / prompt 一律不展开：仅记是否存在 + 数量 + prompt 长度。
        """
        prompt = payload.get("prompt")
        images = payload.get("image")
        return {
            "endpoint": _IMAGE_ENDPOINT,
            "model_name": payload.get("model_name"),
            "aspect_ratio": payload.get("aspect_ratio"),
            "n": payload.get("n"),
            "reference_count": len(images) if isinstance(images, list) else int(bool(images)),
            "prompt_len": len(prompt) if isinstance(prompt, str) else 0,
        }

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        """按实际发送的分辨率与参考图数预估用量。"""
        return per_image_usage(
            output_images=1,
            input_images=facts.reference_count,
            dimensions={"resolution": facts.image_size.upper()} if facts.image_size else None,
        )

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        payload = await asyncio.to_thread(self._build_payload, request)
        logger.info("调用 Kling 图像 API payload=%s", self._safe_log_view(payload))
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            task_id = await self._submit_task(client, _IMAGE_ENDPOINT, payload)
            logger.info("Kling 图像任务已创建: task_id=%s model=%s", task_id, self._model)

            final = await self._poll_until_terminal(
                lambda: self._poll_query(client, f"{_IMAGE_ENDPOINT}/{task_id}"),
                poll_interval=_POLL_INTERVAL_SECONDS,
                max_wait=_POLL_MAX_WAIT_SECONDS,
            )
            download_url = extract_kling_image_urls(final)[0]

        await download_image_to_path(download_url, request.output_path)
        logger.info("Kling 图像下载完成: %s", request.output_path)

        images = payload.get("image")
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_KLING,
            model=self._model,
            image_uri=download_url,
            seed=request.seed,
            usage=per_image_usage(
                output_images=1,
                input_images=len(images) if isinstance(images, list) else int(bool(images)),
                dimensions={"resolution": payload["resolution"].upper()} if "resolution" in payload else None,
            ),
        )


class KlingSingleImageBackend(KlingImageBackend):
    """可灵单参考图通道：``image`` 是纯 base64 字符串。"""

    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = 1
    CHANNEL_REFERENCE_LIMIT_HARD: ClassVar[bool] = True

    def _build_payload(self, request: ImageGenerationRequest) -> dict:
        limit = self.CHANNEL_REFERENCE_LIMIT
        if limit is not None and len(request.reference_images) > limit:
            raise ImageCapabilityError(
                "image_reference_images_exceeded",
                names=excess_image_names(
                    limit, len(request.reference_images), (ref.path for ref in request.reference_images)
                ),
                model=self._model,
                limit=limit,
                count=len(request.reference_images),
            )
        payload = super()._build_payload(request)
        if images := payload.get("image"):
            payload["image"] = images[0]
        return payload


KLING_IMAGE_BACKENDS: dict[str, type[KlingImageBackend]] = {
    "kling-image": KlingImageBackend,
    "kling-image-single": KlingSingleImageBackend,
}
