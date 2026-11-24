"""DashScopeImageBackend — 阿里百炼 Qwen-Image / 万相图像生成后端（同步）。

走原生 multimodal-generation/generation 同步端点，T2I 与 I2I 共用同一请求体，
只差 content 是否含 image 元素。覆盖 qwen-image-2.0 融合系列、qwen-image-edit
编辑系列与 wan2.7-image 系列。schema 依据 docs/api-docs/providers/dashscope.md 所列一手官方文档。
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from arcreel_market_core.aspect_size import IMAGE_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import read_reference_images, should_retry_submit, submit_post
from lib.backends.dashscope_shared import (
    dashscope_headers,
    dashscope_native_base_url,
    extract_image_url,
    resolve_dashscope_api_key,
    safe_body_for_log,
)
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
    download_image_to_path,
    per_image_usage,
)
from lib.backends.providers import PROVIDER_DASHSCOPE
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen-image-2.1-pro"

_IMAGE_ENDPOINT = "/services/aigc/multimodal-generation/generation"

_DEFAULT_WAN_BUDGET = "2K"
_DEFAULT_SHORT_FUSION = 2048
_DEFAULT_SHORT_WAN = 1440
_DEFAULT_SHORT_EDIT = 2048

_STANDARD_PIXEL_BUDGET = 2048 * 2048
_FOURK_PIXEL_BUDGET = 4096 * 4096
_EDIT_MAX_LONG_EDGE = 2048
_DASHSCOPE_MAX_RATIO = 8.0
_EDIT_MAX_RATIO = 4.0


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
        http_timeout: float = 120.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        self._api_key = resolve_dashscope_api_key(api_key)
        self._base_url = dashscope_native_base_url(base_url)
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


DASHSCOPE_IMAGE_BACKENDS = {
    "dashscope-qwen-image": DashScopeImageBackend,
    "dashscope-wan-image": DashScopeWanImageBackend,
    "dashscope-qwen-image-edit": DashScopeQwenEditImageBackend,
}
