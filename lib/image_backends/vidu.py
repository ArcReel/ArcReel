"""ViduImageBackend — Vidu 参考生图 / 文生图后端。

Vidu 图片 API 仅有一个端点 ``/reference2image``：
- ``viduq2``：images 为空时为 T2I，提供 1-7 张参考图时为 I2I / 图片编辑
- ``viduq1``：必须提供 1-7 张参考图，仅 I2I
"""

from __future__ import annotations

import logging
from pathlib import Path

from lib.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    download_image_to_path,
)
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_VIDU
from lib.retry import with_retry_async
from lib.video_backends.base import poll_with_retry
from lib.vidu_shared import (
    VIDU_RETRYABLE_ERRORS,
    assert_vidu_body_size,
    create_vidu_client,
    extract_vidu_url,
    fetch_vidu_task,
    image_to_data_uri,
    is_vidu_done,
    safe_body_for_log,
    vidu_failure_reason,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "viduq2"
_MAX_REFERENCE_IMAGES = 7
_POLL_INTERVAL_SECONDS = 3.0
_POLL_MAX_WAIT_SECONDS = 600.0
_PROMPT_MAX_LEN = 2000  # 文档：不超过 2000 个字符

# 文档明确的 aspect_ratio 白名单
_ASPECT_RATIO_WHITELIST: dict[str, list[str]] = {
    "viduq1": ["16:9", "9:16", "1:1", "3:4", "4:3"],
    "viduq2": ["16:9", "9:16", "1:1", "3:4", "4:3", "21:9", "2:3", "3:2", "auto"],
}
# 文档明确的 resolution 白名单
_RESOLUTION_WHITELIST: dict[str, list[str]] = {
    "viduq1": ["1080p"],
    "viduq2": ["1080p", "2K", "4K"],
}


_MODEL_CAPABILITIES: dict[str, set[ImageCapability]] = {
    "viduq2": {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE},
    "viduq1": {ImageCapability.IMAGE_TO_IMAGE},
}


class ViduImageBackend:
    """Vidu 图片生成后端。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model or DEFAULT_MODEL
        self._capabilities = _MODEL_CAPABILITIES.get(
            self._model, {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
        )

    @property
    def name(self) -> str:
        return PROVIDER_VIDU

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        has_refs = bool(request.reference_images)
        if has_refs and ImageCapability.IMAGE_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_i2i", model=self._model)
        if not has_refs and ImageCapability.TEXT_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_t2i", model=self._model)

        # viduq1 必须 1-7 张；viduq2 允许 0-7 张。这里截断到上限。
        refs = list(request.reference_images or [])
        if len(refs) > _MAX_REFERENCE_IMAGES:
            logger.warning("Vidu 参考图数量 %d 超过上限 %d，截断", len(refs), _MAX_REFERENCE_IMAGES)
            refs = refs[:_MAX_REFERENCE_IMAGES]

        prompt = (request.prompt or "")[:_PROMPT_MAX_LEN]
        body: dict = {
            "model": self._model,
            "prompt": prompt,
        }
        if refs:
            body["images"] = [image_to_data_uri(Path(ref.path)) for ref in refs]

        # aspect_ratio 白名单兜底
        if request.aspect_ratio:
            ar_allowed = _ASPECT_RATIO_WHITELIST.get(self._model)
            if ar_allowed and request.aspect_ratio in ar_allowed:
                body["aspect_ratio"] = request.aspect_ratio
            elif ar_allowed:
                logger.warning(
                    "Vidu image aspect_ratio %s 不在 model=%s 白名单 %s，丢弃",
                    request.aspect_ratio,
                    self._model,
                    ar_allowed,
                )

        # resolution 白名单兜底
        if request.image_size:
            res_allowed = _RESOLUTION_WHITELIST.get(self._model)
            if res_allowed and request.image_size in res_allowed:
                body["resolution"] = request.image_size
            elif res_allowed:
                logger.warning(
                    "Vidu image resolution %s 不在 model=%s 白名单 %s，使用默认",
                    request.image_size,
                    self._model,
                    res_allowed,
                )
        if request.seed is not None:
            body["seed"] = request.seed

        async with create_vidu_client(api_key=self._api_key, base_url=self._base_url) as client:
            payload = await self._create_task(client, body)
            task_id = payload["task_id"]
            credits = payload.get("credits")
            logger.info("Vidu 图片任务已创建: task_id=%s credits=%s", task_id, credits)

            final = await poll_with_retry(
                poll_fn=lambda: fetch_vidu_task(client, task_id),
                is_done=is_vidu_done,
                is_failed=vidu_failure_reason,
                poll_interval=_POLL_INTERVAL_SECONDS,
                max_wait=_POLL_MAX_WAIT_SECONDS,
                retryable_errors=VIDU_RETRYABLE_ERRORS,
                label="Vidu",
                on_progress=lambda v, elapsed: logger.info(
                    "Vidu 图片生成中... state=%s elapsed=%ds", v.get("state"), int(elapsed)
                ),
            )
            url = extract_vidu_url(final)
            # creations 内 credits 可能也带（兜底取顶层）；credits=0 合法，必须显式判 None。
            final_credits = final.get("credits")
            actual_credits = final_credits if final_credits is not None else credits

        await download_image_to_path(url, request.output_path)
        logger.info("Vidu 图片下载完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_VIDU,
            model=self._model,
            seed=request.seed,
            usage_tokens=int(actual_credits) if actual_credits is not None else None,
        )

    @with_retry_async(retryable_errors=VIDU_RETRYABLE_ERRORS)
    async def _create_task(self, client, body: dict) -> dict:
        assert_vidu_body_size(body)
        logger.info("调用 Vidu 图片 API kwargs=%s", format_kwargs_for_log(safe_body_for_log(body)))
        resp = await client.post("/reference2image", json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"Vidu 图片接口 /reference2image 返回 {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        if not data.get("task_id"):
            raise RuntimeError(f"Vidu 图片任务创建响应缺少 task_id: {data}")
        return data
