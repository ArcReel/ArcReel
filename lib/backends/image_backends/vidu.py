"""ViduImageBackend — Vidu 参考生图 / 文生图后端。

Vidu 图片 API 仅有一个端点 ``/reference2image``：images 为空时为 T2I，提供 1-7 张参考图时为 I2I /
图片编辑。调用通道两者都实现；各型号接不接受文生图（如 ``viduq1`` 只接受图生图）是模型事实，记在模型条目上。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import ClassVar

import httpx

from lib.backends.backend_runtime import (
    is_provider_rejection,
    poll_with_retry,
    provider_reason_summary,
    redact_provider_text,
)
from lib.backends.http_status_errors import provider_rejected_error, redacted_status_error
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
    download_image_to_path,
    per_image_usage,
)
from lib.backends.providers import PROVIDER_VIDU
from lib.backends.vidu_shared import (
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
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

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


class ViduImageBackend:
    """Vidu 图片生成后端。"""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES

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

    @property
    def name(self) -> str:
        return PROVIDER_VIDU

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int:
        return _MAX_REFERENCE_IMAGES

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        refs = facts.reference_count
        resolution = facts.image_size or None
        return per_image_usage(
            output_images=1,
            input_images=None if refs is None else min(refs, _MAX_REFERENCE_IMAGES),
            dimensions={"resolution": resolution} if resolution is not None else None,
        )

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
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
            body["images"] = await asyncio.to_thread(lambda: [image_to_data_uri(Path(ref.path)) for ref in refs])

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

        # resolution 以模型目录条目为准，原样透传；越界档位由上游拒绝（docs/adr/0104）
        resolution = request.image_size or None
        if resolution is not None:
            body["resolution"] = resolution
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
            usage=per_image_usage(
                output_images=1,
                input_images=len(refs),
                dimensions={"resolution": resolution} if resolution is not None else None,
            ),
        )

    @with_retry_async(retryable_errors=VIDU_RETRYABLE_ERRORS)
    async def _create_task(self, client, body: dict) -> dict:
        assert_vidu_body_size(body)
        logger.info("调用 Vidu 图片 API kwargs=%s", format_kwargs_for_log(safe_body_for_log(body)))
        resp = await client.post("/reference2image", json=body)
        if resp.status_code >= 400:
            # redacted_status_error 透出 httpx.HTTPStatusError，保留 .response.status_code，
            # 让咽喉层能识别 413 走降档重试；4xx 另带脱敏截断后的拒因摘要，供任务失败信封取用。
            # body 先经脱敏后落日志，保留可诊断性。
            logger.warning(
                "Vidu 图片接口 /reference2image 返回 %s: %s",
                resp.status_code,
                redact_provider_text(resp.text)[:500],
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if is_provider_rejection(resp.status_code):
                    raise provider_rejected_error(exc, provider_reason=provider_reason_summary(resp)) from None
                raise redacted_status_error(exc) from None
        data = resp.json()
        if not data.get("task_id"):
            raise RuntimeError(f"Vidu 图片任务创建响应缺少 task_id: {data}")
        return data
