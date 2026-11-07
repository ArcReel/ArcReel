"""ViduImageBackend: reference-to-image / text-to-image backend.

Vidu has one ``/reference2image`` endpoint: empty images means T2I; 1–7 references mean I2I/editing.
Channels implement both. Model acceptance, such as I2I-only ``viduq1``, is model-entry fact.
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
_PROMPT_MAX_LEN = 2000  # Documented maximum: 2000 characters.

# Explicit documented aspect_ratio allowlist.
_ASPECT_RATIO_WHITELIST: dict[str, list[str]] = {
    "viduq1": ["16:9", "9:16", "1:1", "3:4", "4:3"],
    "viduq2": ["16:9", "9:16", "1:1", "3:4", "4:3", "21:9", "2:3", "3:2", "auto"],
}


class ViduImageBackend:
    """Vidu image backend."""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = _MAX_REFERENCE_IMAGES

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

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        refs = facts.reference_count
        resolution = facts.image_size or None
        return per_image_usage(
            output_images=1,
            input_images=refs,
            dimensions={"resolution": resolution} if resolution is not None else None,
        )

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        refs = list(request.reference_images or [])

        prompt = (request.prompt or "")[:_PROMPT_MAX_LEN]
        body: dict = {
            "model": self._model,
            "prompt": prompt,
        }
        if refs:
            body["images"] = await asyncio.to_thread(lambda: [image_to_data_uri(Path(ref.path)) for ref in refs])

        # aspect_ratio allowlist fallback.
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

        # Pass model-catalog resolution unchanged; upstream rejects invalid tiers (docs/adr/0104).
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
            # credits may also appear in creations, falling back to top-level. Zero is valid; check None explicitly.
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
            # redacted_status_error preserves httpx.HTTPStatusError and .response.status_code,
            # allowing central 413 lower-tier retries. 4xx adds redacted/truncated reasons for task-failure envelopes.
            # Redact bodies before logging while retaining diagnostics.
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
