"""KlingModelPathImageBackend — 可灵新版 API 的普通单图调用通道（端点 ``kling-image-model-path``）。

新版图像 API 把模型名写在路径里，只收 API Key（Bearer），出处见 ``docs/api-docs/endpoints/kling-image-model-path.md``：

- 创建任务 ``POST /image-generation/<模型名>``：``contents[]`` 放提示词（``prompt``）与可选的 1–10 张参考图
  （``refer_image``，纯 base64，附任务内唯一 ``id``）；``settings`` 固定 ``result_type="single"``、``n=1``，
  带 ``resolution``（``2k`` / ``4k`` / ``8k``，默认 ``2k``）与 ``aspect_ratio``。
- 提交响应取 ``data.id``；``GET /tasks?task_ids=<id>`` 轮询到 ``status=succeeded``，取 ``outputs[type=image].url``
  的无水印版本并立即下载本地。
- 计费按输入参考图张数（``input_images``）与输出张数（``output_images=1``，维度 ``resolution`` 为大写档位
  ``2K`` / ``4K`` / ``8K``）表达；预估与结算共用同一套分辨率与比例校验规则。

本通道只支持普通单图生成（一次 1 张）；多张独立图、关联组图、自动决定输出张数与长图接口不在本通道范围内。
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

import httpx

from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import read_reference_images, with_artifact_retry
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageOutputMissingError,
    ImageRequestFacts,
    download_image_to_path,
    per_image_usage,
)
from lib.backends.input_limits import excess_image_names
from lib.backends.kling_backend_base import KlingBackendBase
from lib.backends.kling_shared import (
    is_kling_model_path_terminal,
    kling_model_path_base_url,
    kling_model_path_failure_reason,
    kling_model_path_image_urls,
    kling_model_path_status,
    kling_model_path_task_id,
    kling_response_error,
)
from lib.backends.providers import PROVIDER_KLING
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage

logger = logging.getLogger(__name__)

ENDPOINT_KEY = "kling-image-model-path"
DEFAULT_MODEL = "kling-4.0"

IMAGE_GENERATION_PATH = "image-generation"
TASKS_PATH = "tasks"

_DEFAULT_RESOLUTION = "2K"
_SUPPORTED_RESOLUTIONS: tuple[str, ...] = ("2K", "4K", "8K")
_SUPPORTED_RESOLUTIONS_TEXT = ", ".join(_SUPPORTED_RESOLUTIONS)

_DEFAULT_ASPECT_RATIO = "auto"
_SUPPORTED_ASPECT_RATIOS: tuple[str, ...] = ("16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9", "auto")
_SUPPORTED_ASPECT_RATIOS_TEXT = ", ".join(_SUPPORTED_ASPECT_RATIOS)

_MAX_REFERENCE_IMAGES = 10
_POLL_INTERVAL_SECONDS = 5.0
_POLL_MAX_WAIT_SECONDS = 600.0


def _resolve_resolution(image_size: str | None, model: str) -> str:
    """规范化并校验输出分辨率：缺省取 2K，仅接受 2K / 4K / 8K（大小写不敏感）。"""
    raw = (image_size or "").strip()
    if not raw:
        return _DEFAULT_RESOLUTION
    normalized = raw.upper()
    if normalized not in _SUPPORTED_RESOLUTIONS:
        raise ImageCapabilityError(
            "image_resolution_not_supported",
            model=model,
            resolution=raw,
            supported=_SUPPORTED_RESOLUTIONS_TEXT,
        )
    return normalized


def _resolve_aspect_ratio(aspect_ratio: str | None, model: str) -> str:
    """规范化并校验画面比例：缺省取 auto，仅接受官方枚举。"""
    raw = (aspect_ratio or "").strip()
    if not raw:
        return _DEFAULT_ASPECT_RATIO
    normalized = raw.lower()
    if normalized not in _SUPPORTED_ASPECT_RATIOS:
        raise ImageCapabilityError(
            "image_aspect_ratio_not_supported",
            model=model,
            aspect_ratio=raw,
            supported=_SUPPORTED_ASPECT_RATIOS_TEXT,
        )
    return normalized


class KlingModelPathImageBackend(KlingBackendBase):
    """可灵新版 API 普通单图后端（Bearer API Key，异步轮询）。"""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = _MAX_REFERENCE_IMAGES
    CHANNEL_REFERENCE_LIMIT_HARD: ClassVar[bool] = True

    _media_label = "图像"

    def __init__(
        self,
        *,
        auth_mode: str = "bearer",
        access_key: str | None = None,
        secret_key: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        api_model_name: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        resolved_model = model or DEFAULT_MODEL
        if auth_mode == "jwt":
            raise ImageCapabilityError("image_kling_api_key_required", model=resolved_model)
        if auth_mode != "bearer":
            raise ValueError(f"未知 Kling auth_mode: {auth_mode}")
        super().__init__(
            auth_mode=auth_mode,
            access_key=access_key,
            secret_key=secret_key,
            api_key=api_key,
            model=resolved_model,
            base_url=base_url,
            http_timeout=http_timeout,
            transport_layer=transport_layer,
        )
        self._api_model_name = api_model_name or self._model
        self._base_url = kling_model_path_base_url(base_url)

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    def _task_id_from(self, submit_payload: dict) -> str:
        return kling_model_path_task_id(submit_payload)

    def _task_status(self, payload: dict) -> str | None:
        return kling_model_path_status(payload)

    def _is_terminal(self, payload: dict) -> bool:
        return is_kling_model_path_terminal(payload)

    def _failure_reason(self, payload: dict) -> str | None:
        return kling_model_path_failure_reason(payload)

    def _build_payload(self, request: ImageGenerationRequest) -> tuple[str, dict]:
        """返回 (提交路径, 请求体)；超出官方分辨率、比例或参考图约束时在提交前拒绝。"""
        resolution = _resolve_resolution(request.image_size, self._model)
        aspect_ratio = _resolve_aspect_ratio(request.aspect_ratio, self._model)
        refs = request.reference_images
        if len(refs) > _MAX_REFERENCE_IMAGES:
            raise ImageCapabilityError(
                "image_reference_images_exceeded",
                names=excess_image_names(_MAX_REFERENCE_IMAGES, len(refs), (ref.path for ref in refs)),
                model=self._model,
                limit=_MAX_REFERENCE_IMAGES,
                count=len(refs),
            )

        contents: list[dict[str, str]] = [{"type": "prompt", "text": request.prompt}]
        if refs:
            encoded = read_reference_images((ref.path for ref in refs), self._model)
            for index, image in enumerate(encoded, start=1):
                contents.append({"type": "refer_image", "url": image.base64(), "id": f"image_{index}"})

        payload = {
            "contents": contents,
            "settings": {
                "result_type": "single",
                "n": 1,
                "resolution": resolution.lower(),
                "aspect_ratio": aspect_ratio,
            },
        }
        return f"{IMAGE_GENERATION_PATH}/{self._api_model_name}", payload

    @staticmethod
    def _safe_log_view(path: str, payload: dict) -> dict:
        """预脱敏标量视图：参考图 base64 与提示词正文一律不展开。"""
        raw_settings = payload.get("settings")
        settings: dict = raw_settings if isinstance(raw_settings, dict) else {}
        raw_contents = payload.get("contents")
        contents: list = raw_contents if isinstance(raw_contents, list) else []
        prompt = next((c.get("text") for c in contents if isinstance(c, dict) and c.get("type") == "prompt"), "")
        ref_count = sum(1 for c in contents if isinstance(c, dict) and c.get("type") == "refer_image")
        return {
            "endpoint": path,
            "result_type": settings.get("result_type"),
            "n": settings.get("n"),
            "resolution": settings.get("resolution"),
            "aspect_ratio": settings.get("aspect_ratio"),
            "reference_count": ref_count,
            "prompt_len": len(prompt) if isinstance(prompt, str) else 0,
        }

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        """按实际发送的分辨率与参考图数预估用量；不支持的分辨率或比例保持未定价，不静默回落默认档。"""
        raw_size = (facts.image_size or "").strip()
        try:
            resolution = _resolve_resolution(facts.image_size, facts.model)
        except ImageCapabilityError:
            return Usage(dimensions={"resolution": raw_size}) if raw_size else Usage()
        try:
            _resolve_aspect_ratio(facts.aspect_ratio, facts.model)
        except ImageCapabilityError:
            return Usage()
        if facts.reference_count is not None and facts.reference_count > _MAX_REFERENCE_IMAGES:
            return Usage()
        return per_image_usage(
            output_images=1,
            input_images=facts.reference_count,
            dimensions={"resolution": resolution},
        )

    async def _query_task(self, client: httpx.AsyncClient, task_id: str) -> dict:
        resp = await client.get(
            f"{self._base_url}/{TASKS_PATH}",
            params={"task_ids": task_id},
            headers=self._headers(),
        )
        raise_for_status_redacted(resp)
        return resp.json()

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        path, payload = await asyncio.to_thread(self._build_payload, request)
        logger.info("调用 Kling 新版图像 API payload=%s", self._safe_log_view(path, payload))
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            task_id = await self._submit_task(client, path, payload)
            logger.info("Kling 图像任务已创建: task_id=%s model=%s", task_id, self._model)

            final = await self._poll_until_terminal(
                lambda: self._query_task(client, task_id),
                poll_interval=_POLL_INTERVAL_SECONDS,
                max_wait=_POLL_MAX_WAIT_SECONDS,
            )

        urls = kling_model_path_image_urls(final)
        if not urls:
            reason = kling_response_error(final)
            if reason is not None:
                raise RuntimeError(reason)
            raise ImageOutputMissingError(model=self._model)
        download_url = urls[0]

        await with_artifact_retry(
            lambda: download_image_to_path(download_url, request.output_path),
            label="Kling 图像",
        )
        logger.info("Kling 图像下载完成: %s", request.output_path)

        reference_count = sum(1 for item in payload["contents"] if item.get("type") == "refer_image")
        resolution = str(payload["settings"]["resolution"]).upper()
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_KLING,
            model=self._model,
            image_uri=download_url,
            seed=request.seed,
            usage=per_image_usage(
                output_images=1,
                input_images=reference_count,
                dimensions={"resolution": resolution},
            ),
        )
