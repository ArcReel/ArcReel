"""HiggsfieldImageBackend — Higgsfield 图片生成后端。

Higgsfield 是聚合多家模型的统一 API，每个模型一组固定的调用端点，按 API 模型名分派：
- ``higgsfield-ai/soul/v2``  → ``/higgsfield-ai/soul/v2/standard``（仅文生图）
- ``alibaba/qwen-image-3``   → ``/alibaba/qwen-image-3/text-to-image``；带参考图时 ``/alibaba/qwen-image-3/edit``

参考图只收公开 URL，提交前先经文件上传接口换成 URL（``higgsfield_shared.upload_image``）。提交后按
``request_id`` 轮询 ``/requests/{id}/status`` 到终态，再下载 ``images[0].url``。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar

from lib.backends.backend_runtime import (
    notify_provider_response_to,
    poll_with_retry,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)
from lib.backends.higgsfield_shared import (
    create_higgsfield_client,
    extract_image_urls,
    extract_request_id,
    fetch_request_status,
    is_request_done,
    new_idempotency_key,
    request_failure_reason,
    safe_body_for_log,
    upload_image,
)
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
from lib.backends.providers import PROVIDER_HIGGSFIELD
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

MODEL_SOUL_2 = "higgsfield-ai/soul/v2"
MODEL_QWEN_IMAGE_3 = "alibaba/qwen-image-3"
DEFAULT_MODEL = MODEL_QWEN_IMAGE_3

_POLL_INTERVAL_SECONDS = 3.0
_POLL_MAX_WAIT_SECONDS = 600.0

# 各模型的参考图上限（官方 schema）：Soul 2 没有参考图字段；Qwen Image 3 的 edit 收 1–3 张。
_MAX_REFERENCE_IMAGES: dict[str, int] = {
    MODEL_SOUL_2: 0,
    MODEL_QWEN_IMAGE_3: 3,
}

# 官方 schema 明确的 aspect_ratio 枚举。
_ASPECT_RATIO_WHITELIST: dict[str, frozenset[str]] = {
    MODEL_SOUL_2: frozenset({"9:16", "16:9", "4:3", "3:4", "1:1", "2:3", "3:2"}),
    MODEL_QWEN_IMAGE_3: frozenset({"1:1", "2:3", "3:2", "3:4", "4:3", "7:9", "9:7", "9:16", "16:9", "21:9"}),
}

# 官方 schema 的 seed 取值区间；越界不发（供应商按随机处理），不改写成别的值。
_SEED_RANGE: dict[str, tuple[int, int]] = {
    MODEL_SOUL_2: (1, 1_000_000),
    MODEL_QWEN_IMAGE_3: (0, 2_147_483_647),
}


def _known_model(model: str) -> str | None:
    return model if model in _MAX_REFERENCE_IMAGES else None


def _endpoint_for(model: str, reference_count: int) -> str:
    if model == MODEL_SOUL_2:
        return "/higgsfield-ai/soul/v2/standard"
    if model == MODEL_QWEN_IMAGE_3:
        return "/alibaba/qwen-image-3/edit" if reference_count else "/alibaba/qwen-image-3/text-to-image"
    # 认不得的模型：把 API 模型名当端点前缀，文生图走 text-to-image、图生图走 edit（Higgsfield 多数
    # 第三方模型的端点按此命名）。拼错由上游 404 报出。
    return f"/{model.strip('/')}/{'edit' if reference_count else 'text-to-image'}"


class HiggsfieldImageBackend:
    """Higgsfield 图片生成后端。"""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES

    def __init__(
        self,
        *,
        access_key: str | None = None,
        secret_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
    ):
        self._access_key = access_key
        self._secret_key = secret_key
        self._base_url = base_url
        self._transport_layer = transport_layer
        self._model = model or DEFAULT_MODEL
        self._known_model = _known_model(self._model)

    @property
    def name(self) -> str:
        return PROVIDER_HIGGSFIELD

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int:
        # 认不得的模型不按数量裁剪（0 = 不裁剪），由上游判定。
        return _MAX_REFERENCE_IMAGES.get(self._model, 0)

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        limit = _MAX_REFERENCE_IMAGES.get(facts.model)
        refs = facts.reference_count
        if refs is not None and limit:
            refs = min(refs, limit)
        resolution = facts.image_size or None
        return per_image_usage(
            output_images=1,
            input_images=refs,
            dimensions={"resolution": resolution} if resolution is not None else None,
        )

    def _build_body(self, request: ImageGenerationRequest, reference_urls: list[str]) -> dict:
        body: dict = {"prompt": request.prompt or ""}
        if reference_urls:
            body["image_urls"] = reference_urls
        if request.aspect_ratio:
            allowed = _ASPECT_RATIO_WHITELIST.get(self._model)
            if allowed is None or request.aspect_ratio in allowed:
                body["aspect_ratio"] = request.aspect_ratio
            else:
                # 不改写成别的比例也不报错：比例在 Higgsfield 这里只是输出裁切，缺省由供应商取默认。
                logger.warning(
                    "Higgsfield image aspect_ratio %s 不在 model=%s 白名单 %s，不发送",
                    request.aspect_ratio,
                    self._model,
                    sorted(allowed),
                )
        # resolution 以模型目录条目为准，原样透传；越界档位由上游拒绝（docs/adr/0104）
        if request.image_size:
            body["resolution"] = request.image_size
        if request.seed is not None:
            low, high = _SEED_RANGE.get(self._model, (0, 2_147_483_647))
            if low <= request.seed <= high:
                body["seed"] = request.seed
            else:
                logger.warning("Higgsfield image seed %s 越界 [%s, %s]，不发送", request.seed, low, high)
        return body

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        refs = list(request.reference_images or [])
        limit = _MAX_REFERENCE_IMAGES.get(self._model)
        if limit == 0 and refs:
            raise ImageCapabilityError("image_capability_missing_i2i", provider=self.name, model=self._model)
        if limit and len(refs) > limit:
            logger.warning("Higgsfield 参考图数量 %d 超过 model=%s 上限 %d，截断", len(refs), self._model, limit)
            refs = refs[:limit]
        endpoint = _endpoint_for(self._model, len(refs))
        resolution = request.image_size or None

        async with create_higgsfield_client(
            access_key=self._access_key,
            secret_key=self._secret_key,
            base_url=self._base_url,
            transport_layer=self._transport_layer,
        ) as client:
            reference_urls = [await upload_image(client, Path(ref.path)) for ref in refs]
            body = self._build_body(request, reference_urls)
            payload = await self._create_request(client, endpoint, body, new_idempotency_key())
            await notify_provider_response_to(request.on_provider_response, "submit", payload)
            request_id = extract_request_id(payload)
            logger.info("Higgsfield 图片请求已创建: endpoint=%s request_id=%s", endpoint, request_id)

            final = await poll_with_retry(
                poll_fn=lambda: fetch_request_status(client, request_id),
                is_done=is_request_done,
                is_failed=request_failure_reason,
                poll_interval=_POLL_INTERVAL_SECONDS,
                max_wait=_POLL_MAX_WAIT_SECONDS,
                retry_if=should_retry_poll,
                label="Higgsfield",
                on_progress=lambda v, elapsed: logger.info(
                    "Higgsfield 图片生成中... status=%s elapsed=%ds", v.get("status"), int(elapsed)
                ),
            )
            await notify_provider_response_to(request.on_provider_response, "result", final)
            urls = extract_image_urls(final)
            if not urls:
                raise ImageOutputMissingError(model=self._model)

        await download_image_to_path(urls[0], request.output_path)
        logger.info("Higgsfield 图片下载完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_HIGGSFIELD,
            model=self._model,
            seed=request.seed,
            usage=per_image_usage(
                output_images=1,
                input_images=len(refs),
                dimensions={"resolution": resolution} if resolution is not None else None,
            ),
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _create_request(self, client, endpoint: str, body: dict, idempotency_key: str) -> dict:
        logger.info(
            "调用 Higgsfield 图片 API endpoint=%s kwargs=%s",
            endpoint,
            format_kwargs_for_log(safe_body_for_log(body)),
        )
        # 提交是「创建 + 计费」调用：submit_post 把歧义态转终态失败、不重试；仅连接建立失败重试，
        # 重试带同一把 Idempotency-Key，供应商据此不重复建任务。
        resp = await submit_post(
            lambda: client.post(endpoint, json=body, headers={"Idempotency-Key": idempotency_key}),
            provider=PROVIDER_HIGGSFIELD,
        )
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Higgsfield 提交响应不是对象: {type(data).__name__}")
        return data
