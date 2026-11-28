"""DashScopeImageBackend — 阿里百炼图像生成后端（同步 multimodal 与异步 Vidu image-generation）。

同步端点走原生 ``multimodal-generation/generation``，T2I 与 I2I 共用同一请求体，只差 content 是否含
image 元素；覆盖 qwen-image-2.0 融合系列、qwen-image-edit 编辑系列与 wan2.7-image 系列。
异步 Vidu 端点走原生 ``image-generation/generation``（带 ``X-DashScope-Async: enable``）与
``GET /tasks/{task_id}`` 轮询，支持文生图、参考生图与图片编辑，最多 14 张参考图与 1K/2K/4K 规格。
schema 依据 docs/api-docs/providers/dashscope.md 所列一手官方文档。
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

import httpx

from arcreel_market_core.aspect_size import IMAGE_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import (
    poll_with_retry,
    read_reference_images,
    should_retry_poll,
    should_retry_submit,
    submit_post,
    with_artifact_retry,
)
from lib.backends.dashscope_shared import (
    dashscope_failure_reason,
    dashscope_headers,
    dashscope_native_base_url,
    extract_image_url,
    extract_image_url_or_none,
    extract_task_id,
    is_dashscope_expired,
    is_dashscope_terminal,
    resolve_dashscope_api_key,
    safe_body_for_log,
)
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
from lib.backends.providers import PROVIDER_DASHSCOPE
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import DEFAULT_BACKOFF_SECONDS, DEFAULT_MAX_ATTEMPTS, with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen-image-2.1-pro"

_IMAGE_ENDPOINT = "/services/aigc/multimodal-generation/generation"
ASYNC_IMAGE_ENDPOINT = "/services/aigc/image-generation/generation"
VIDU_IMAGE_ENDPOINT_KEY = "dashscope-vidu-image"

_DEFAULT_WAN_BUDGET = "2K"
_DEFAULT_SHORT_FUSION = 2048
_DEFAULT_SHORT_WAN = 1440
_DEFAULT_SHORT_EDIT = 2048

_STANDARD_PIXEL_BUDGET = 2048 * 2048
_FOURK_PIXEL_BUDGET = 4096 * 4096
_EDIT_MAX_LONG_EDGE = 2048
_DASHSCOPE_MAX_RATIO = 8.0
_EDIT_MAX_RATIO = 4.0

_VIDU_DEFAULT_MODEL = "vidu/viduq3.1-fast_reference2image"
_VIDU_DEFAULT_RESOLUTION = "1K"
_VIDU_SUPPORTED_RESOLUTIONS: tuple[str, ...] = ("1K", "2K", "4K")
_VIDU_SUPPORTED_RESOLUTIONS_TEXT = ", ".join(_VIDU_SUPPORTED_RESOLUTIONS)
_VIDU_DEFAULT_ASPECT_RATIO = "auto"
_VIDU_SUPPORTED_ASPECT_RATIOS: tuple[str, ...] = (
    "auto",
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
    "1:4",
    "4:1",
    "1:8",
    "8:1",
)
_VIDU_SUPPORTED_ASPECT_RATIOS_TEXT = ", ".join(_VIDU_SUPPORTED_ASPECT_RATIOS)
_VIDU_MAX_REFERENCE_IMAGES = 14
_VIDU_POLL_INTERVAL_SECONDS = 5.0
_VIDU_POLL_MAX_WAIT_SECONDS = 600.0


def _resolve_vidu_resolution(image_size: str | None, model: str) -> str:
    """规范化并校验 Vidu 图片输出分辨率：缺省取 1K，仅接受 1K / 2K / 4K。"""
    raw = (image_size or "").strip()
    if not raw:
        return _VIDU_DEFAULT_RESOLUTION
    normalized = raw.upper()
    if normalized not in _VIDU_SUPPORTED_RESOLUTIONS:
        raise ImageCapabilityError(
            "image_resolution_not_supported",
            model=model,
            resolution=raw,
            supported=_VIDU_SUPPORTED_RESOLUTIONS_TEXT,
        )
    return normalized


def _resolve_vidu_aspect_ratio(aspect_ratio: str | None, model: str) -> str:
    """规范化并校验 Vidu 图片画幅比例：缺省取 auto，仅接受官方枚举。"""
    raw = (aspect_ratio or "").strip()
    if not raw:
        return _VIDU_DEFAULT_ASPECT_RATIO
    normalized = raw.lower()
    if normalized not in _VIDU_SUPPORTED_ASPECT_RATIOS:
        raise ImageCapabilityError(
            "image_aspect_ratio_not_supported",
            model=model,
            aspect_ratio=raw,
            supported=_VIDU_SUPPORTED_ASPECT_RATIOS_TEXT,
        )
    return normalized


def _extract_vidu_image_count(payload: dict) -> int | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    raw = usage.get("image_count")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    return raw


def _extract_vidu_resolution_tier(payload: dict) -> str | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    raw = usage.get("SR")
    if isinstance(raw, str) and raw.strip().upper() in _VIDU_SUPPORTED_RESOLUTIONS:
        return raw.strip().upper()
    return None


class DashScopeImageBackend:
    """阿里百炼图像后端（同步 multimodal 端点）。"""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = None

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        wan3_base_url: str | None = None,
        http_timeout: float = 120.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        self._api_key = resolve_dashscope_api_key(api_key)
        self._base_url = dashscope_native_base_url(base_url)
        cleaned_maas = (wan3_base_url or "").strip()
        self._wan3_base_url = dashscope_native_base_url(cleaned_maas) if cleaned_maas else None
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._transport_layer = transport_layer

    @staticmethod
    def _exceeds_standard_budget(size: str) -> bool:
        """size 是否超出标准档总像素预算（2048×2048）。

        docs 口径：超出 2048×2048 的输出仅 wan2.7-image-pro 文生图支持（4K 档=4096×4096）。
        档位 "1K"/"2K" 在预算内、"4K" 超预算；像素值按"总像素 > 预算"判定，避免只认 "4K"
        字面而让 "4096*4096" / "3000*3000" 等数字写法绕过门控（这是按比例算总像素，
        故 "4096*512" 这类窄幅合法尺寸不会被误拒）。
        """
        normalized = size.strip().upper()
        if normalized in ("1K", "2K"):
            return False
        if normalized == "4K":
            return True
        for sep in ("*", "X", "×"):
            if sep in normalized:
                parts = normalized.split(sep, 1)
                try:
                    return int(parts[0]) * int(parts[1]) > _STANDARD_PIXEL_BUDGET
                except ValueError:
                    return False
        return False

    @property
    def name(self) -> str:
        return PROVIDER_DASHSCOPE

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
        """预估只报张数：百炼图片按张统一价，尺寸不影响费用。"""
        return per_image_usage(output_images=1, input_images=facts.reference_count)

    @with_retry_async(retry_if=should_retry_submit)
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        has_refs = bool(request.reference_images)
        size = self._resolve_size(request, has_refs)
        content = await asyncio.to_thread(self._build_content, request, has_refs)

        parameters: dict = {
            "n": 1,
            "watermark": False,
            "prompt_extend": False,
            "size": size,
        }
        if request.seed is not None:
            parameters["seed"] = request.seed

        payload = {
            "model": self._model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": parameters,
        }

        logger.info(
            "调用 %s 图片 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_IMAGE_ENDPOINT}",
                    json=payload,
                    headers=dashscope_headers(self._api_key),
                ),
                provider=PROVIDER_DASHSCOPE,
            )
            data = resp.json()

        url = extract_image_url(data)
        await download_image_to_path(url, request.output_path)
        logger.info("DashScope 图片生成完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_DASHSCOPE,
            model=self._model,
            image_uri=url,
            usage=per_image_usage(
                output_images=1,
                input_images=sum(1 for part in content if "image" in part),
                dimensions={"resolution": size},
            ),
        )

    def _resolve_size(self, request: ImageGenerationRequest, has_refs: bool) -> str:
        """按「比例优先、清晰度其次」算出 宽*高。

        比例永远来自 aspect_ratio；image_size（档位词 / 自定义 宽*高 / None）只决定清晰度短边，
        自定义值剥离其自带比例（取 min）。各族按自身像素约束精确收口：
          - wan 系：方式二像素值（接受任意、对齐 16），总像素 ≤ 标准 2048² / 4K 4096²；
            绝不下传档位词（wan 文生图会被强制输出正方形、丢比例）。
          - qwen-image-2.0 融合系列：总像素 ∈ [512², 2048²]，自由设宽高。
          - qwen-image-edit-plus / -max 编辑系列：宽高均 ∈ [512, 2048]。
        经典系列（qwen-image / -plus / -max）仅 5 固定档、不接受任意像素，未注册预设、不推荐，
        不在本机制覆盖内（见 docs/adr/0011）。
        """
        explicit = (request.image_size or "").strip()
        aspect = request.aspect_ratio

        short = resolution_to_short_edge(
            explicit or None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=_DEFAULT_SHORT_FUSION
        )
        w, h = aspect_size(
            aspect, short, round_to=16, max_total_pixels=_STANDARD_PIXEL_BUDGET, max_ratio=_DASHSCOPE_MAX_RATIO
        )
        return f"{w}*{h}"

    def _build_content(self, request: ImageGenerationRequest, has_refs: bool) -> list[dict]:
        content: list[dict] = []
        if has_refs:
            data_uris = [
                image.data_uri()
                for image in read_reference_images((ref.path for ref in request.reference_images), self.model)
            ]
            content.extend({"image": uri} for uri in data_uris)
        content.append({"text": request.prompt})
        return content


class DashScopeWanImageBackend(DashScopeImageBackend):
    """Wan 任意像素请求，带参考图时只用标准像素预算。"""

    def _resolve_size(self, request: ImageGenerationRequest, has_refs: bool) -> str:
        explicit = (request.image_size or "").strip()
        aspect = request.aspect_ratio
        budget_word = explicit or _DEFAULT_WAN_BUDGET
        exceeds = self._exceeds_standard_budget(budget_word)
        if exceeds and has_refs:
            raise ImageCapabilityError("image_dashscope_4k_t2i_only", model=self._model)
        max_total = _FOURK_PIXEL_BUDGET if exceeds else _STANDARD_PIXEL_BUDGET
        short = resolution_to_short_edge(
            explicit or None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=_DEFAULT_SHORT_WAN
        )
        w, h = aspect_size(aspect, short, round_to=16, max_total_pixels=max_total, max_ratio=_DASHSCOPE_MAX_RATIO)
        return f"{w}*{h}"


class DashScopeQwenEditImageBackend(DashScopeImageBackend):
    """Qwen 编辑请求：宽高均不超过 2048。"""

    def _resolve_size(self, request: ImageGenerationRequest, has_refs: bool) -> str:
        explicit = (request.image_size or "").strip()
        aspect = request.aspect_ratio
        short = resolution_to_short_edge(
            explicit or None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=_DEFAULT_SHORT_EDIT
        )
        w, h = aspect_size(aspect, short, round_to=16, max_long_edge=_EDIT_MAX_LONG_EDGE, max_ratio=_EDIT_MAX_RATIO)
        return f"{w}*{h}"


class DashScopeViduImageBackend(DashScopeImageBackend):
    """百炼 Vidu 异步图像生成后端（支持文生图、参考生图与图片编辑，1K/2K/4K 档位，最多 14 张参考图）。"""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = _VIDU_MAX_REFERENCE_IMAGES
    CHANNEL_REFERENCE_LIMIT_HARD: ClassVar[bool] = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        wan3_base_url: str | None = None,
        http_timeout: float = 60.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model or _VIDU_DEFAULT_MODEL,
            base_url=base_url,
            wan3_base_url=wan3_base_url,
            http_timeout=http_timeout,
            transport_layer=transport_layer,
        )

    @property
    def _request_base_url(self) -> str:
        """业务空间专属 MaaS 地址优先，留空时回落通用 base_url。"""
        if self._wan3_base_url:
            return self._wan3_base_url
        return self._base_url

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        """按实际发送的 1K/2K/4K 档位与参考图张数预估；非法规格或超限参考图保持未定价。"""
        raw_size = (facts.image_size or "").strip()
        try:
            resolution = _resolve_vidu_resolution(facts.image_size, facts.model)
        except ImageCapabilityError:
            return Usage(dimensions={"resolution": raw_size}) if raw_size else Usage()
        try:
            _resolve_vidu_aspect_ratio(facts.aspect_ratio, facts.model)
        except ImageCapabilityError:
            return Usage()
        if facts.reference_count is not None and facts.reference_count > _VIDU_MAX_REFERENCE_IMAGES:
            return Usage()
        return per_image_usage(
            output_images=1,
            input_images=facts.reference_count,
            dimensions={"resolution": resolution},
        )

    def _build_async_payload(self, request: ImageGenerationRequest) -> tuple[dict, str, int]:
        resolution = _resolve_vidu_resolution(request.image_size, self._model)
        aspect_ratio = _resolve_vidu_aspect_ratio(request.aspect_ratio, self._model)
        refs = request.reference_images
        if len(refs) > _VIDU_MAX_REFERENCE_IMAGES:
            raise ImageCapabilityError(
                "image_reference_images_exceeded",
                names=excess_image_names(_VIDU_MAX_REFERENCE_IMAGES, len(refs), (ref.path for ref in refs)),
                model=self._model,
                limit=_VIDU_MAX_REFERENCE_IMAGES,
                count=len(refs),
            )
        content: list[dict[str, str]] = [{"text": request.prompt}]
        if refs:
            encoded = read_reference_images((ref.path for ref in refs), self._model)
            content.extend({"image": image.data_uri()} for image in encoded)

        parameters: dict[str, object] = {
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "n": 1,
            "watermark": False,
        }
        if request.seed is not None:
            parameters["seed"] = request.seed

        payload = {
            "model": self._model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": parameters,
        }
        return payload, resolution, len(refs)

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(self, client: httpx.AsyncClient, payload: dict) -> str:
        resp = await submit_post(
            lambda: client.post(
                f"{self._request_base_url}{ASYNC_IMAGE_ENDPOINT}",
                json=payload,
                headers=dashscope_headers(self._api_key, async_mode=True),
            ),
            provider=PROVIDER_DASHSCOPE,
        )
        return extract_task_id(resp.json())

    async def _poll_once(self, client: httpx.AsyncClient, task_id: str) -> dict:
        resp = await client.get(
            f"{self._request_base_url}/tasks/{task_id}",
            headers=dashscope_headers(self._api_key),
        )
        raise_for_status_redacted(resp)
        return resp.json()

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        payload, resolution, reference_count = await asyncio.to_thread(self._build_async_payload, request)
        logger.info(
            "调用 %s 异步图像 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            task_id = await self._create_task(client, payload)
            logger.info("DashScope 图像任务已创建: task_id=%s model=%s", task_id, self._model)

            final = await poll_with_retry(
                poll_fn=lambda: self._poll_once(client, task_id),
                is_done=is_dashscope_terminal,
                is_failed=dashscope_failure_reason,
                poll_interval=_VIDU_POLL_INTERVAL_SECONDS,
                max_wait=_VIDU_POLL_MAX_WAIT_SECONDS,
                retry_if=should_retry_poll,
                label="DashScope 图像",
                on_progress=lambda v, elapsed: logger.info(
                    "DashScope 图像生成中... status=%s elapsed=%ds",
                    (v.get("output") or {}).get("task_status"),
                    int(elapsed),
                ),
            )

        if is_dashscope_expired(final):
            raise RuntimeError(f"DashScope 图像任务已过期: {task_id}")

        url = extract_image_url_or_none(final)
        if url is None:
            reason = dashscope_failure_reason(final)
            if reason is not None:
                raise RuntimeError(reason)
            raise ImageOutputMissingError(model=self._model)

        await with_artifact_retry(
            lambda: download_image_to_path(url, request.output_path),
            label="DashScope 图像",
        )
        logger.info("DashScope 异步图像下载完成: %s", request.output_path)

        output_images = _extract_vidu_image_count(final) or 1
        billed_resolution = _extract_vidu_resolution_tier(final) or resolution
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_DASHSCOPE,
            model=self._model,
            image_uri=url,
            seed=request.seed,
            usage=per_image_usage(
                output_images=output_images,
                input_images=reference_count,
                dimensions={"resolution": billed_resolution},
            ),
        )


DASHSCOPE_IMAGE_BACKENDS = {
    "dashscope-qwen-image": DashScopeImageBackend,
    "dashscope-wan-image": DashScopeWanImageBackend,
    "dashscope-qwen-image-edit": DashScopeQwenEditImageBackend,
}
