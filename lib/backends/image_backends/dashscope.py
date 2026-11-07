"""DashScopeImageBackend: synchronous Alibaba Bailian Qwen-Image / Wan generation.

Native synchronous multimodal-generation/generation shares T2I/I2I bodies, differing only by image
content elements. Cover unified qwen-image-2.0, qwen-image-edit, and wan2.7-image families. Official
schema sources are indexed in docs/api-docs/providers/dashscope.md.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import ClassVar

from arcreel_market_core.aspect_size import IMAGE_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import should_retry_submit, submit_post
from lib.backends.dashscope_shared import (
    dashscope_headers,
    dashscope_native_base_url,
    extract_image_url,
    image_to_data_uri,
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

DEFAULT_MODEL = "qwen-image-2.0"

_IMAGE_ENDPOINT = "/services/aigc/multimodal-generation/generation"

# Default short edges by family without resolution. aspect_ratio always supplies ratio;
# arcreel_market_core.aspect_size solves exact dimensions under family limits (docs/adr/0011), avoiding inexact static tables.
#   - Unified Qwen native 2048²; Wan 2K; editing naturally constrained by max_long_edge.
_DEFAULT_WAN_BUDGET = "2K"
_DEFAULT_SHORT_FUSION = 2048
_DEFAULT_SHORT_WAN = 1440
_DEFAULT_SHORT_EDIT = 2048

# Standard total budget, nonpro/non-T2I cap, 2048×2048; excess requires wan2.7-image-pro T2I, 4K=4096×4096.
_STANDARD_PIXEL_BUDGET = 2048 * 2048
_FOURK_PIXEL_BUDGET = 4096 * 4096
# qwen-image-edit-plus / -max both dimensions [512, 2048].
_EDIT_MAX_LONG_EDGE = 2048
# DashScope generation ratios support 1:8 ~ 8:1.
_DASHSCOPE_MAX_RATIO = 8.0
# Editing dimensions [512, 2048] constrain ratios to 2048/512 = 4:1, narrower than generation.
_EDIT_MAX_RATIO = 4.0


class DashScopeImageBackend:
    """Synchronous Alibaba Bailian multimodal image backend."""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    # Channels impose no reference counts; Qwen/Wan model-specific maxima live in entries.
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
        """Whether size exceeds standard total pixels, 2048×2048.

        Documentation permits excess only for wan2.7-image-pro T2I, 4K=4096×4096. Tiers 1K/2K fit,
        4K exceeds. Numeric dimensions compare total pixels to budgets, preventing 4096*4096 / 3000*3000
        from bypassing literal-4K checks, while correctly allowing narrow 4096*512 dimensions.
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
        """Estimate image counts only: uniform Bailian per-image pricing ignores sizes."""
        return per_image_usage(output_images=1, input_images=facts.reference_count)

    @with_retry_async(retry_if=should_retry_submit)
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        has_refs = bool(request.reference_images)
        size = self._resolve_size(request, has_refs)
        content = await asyncio.to_thread(self._build_content, request, has_refs)

        parameters: dict = {
            "n": 1,
            "watermark": False,
            # ArcReel prompts are already LLM-refined; disable optimization to retain intent.
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
            # Synchronous image POST creates/bills nonidempotently. submit_post terminally converts ambiguous transport, possibly
            # delivered requests with lost responses, into AmbiguousSubmitError, avoiding double billing. >=400 logs bodies
            # and raises HTTPStatusError, retaining status_code for central 413 lower-tier fallback. should_retry_submit
            # routes statuses: 4xx fail fast; 5xx/429 retry.
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
        """Compute width*height, prioritizing ratio over resolution.

        aspect_ratio alone determines ratio; image_size tiers/custom width*height/None determine short-edge
        resolution. Strip custom ratios through min dimensions, respecting family-specific pixel constraints:
          - Wan: method-two arbitrary pixels aligned to 16, total ≤ standard 2048² / 4K 4096².
            Never send tier labels, which force square Wan T2I and lose ratios.
          - Unified qwen-image-2.0: total pixels [512², 2048²], free dimensions.
          - qwen-image-edit-plus / -max: both dimensions [512, 2048].
        Classic qwen-image / -plus / -max accept only five fixed tiers, not arbitrary pixels. Not registered
        as presets or recommended, outside this mechanism (docs/adr/0011).
        """
        explicit = (request.image_size or "").strip()
        aspect = request.aspect_ratio

        # Unified qwen-image-2.0 family.
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
            # Fail loudly if any declared reference is missing, including directories/empty paths resolving to ".", or unreadable
            # through permissions/concurrent deletion OSError. List filenames so users see unused images instead of
            # silently generating and billing incorrect output from subsets.
            data_uris: list[str] = []
            unreadable: list[str] = []
            # names enter multilingual en/vi error templates: use locale-neutral separators/placeholders.
            # Empty paths lack filenames; use reference ordinal #N, never leak Chinese placeholders into other locales.
            for idx, ref in enumerate(request.reference_images, start=1):
                path = Path(ref.path) if ref.path else None
                if path is None or not path.is_file():
                    unreadable.append(path.name if path else f"#{idx}")
                    continue
                try:
                    data_uris.append(image_to_data_uri(path))
                except OSError as exc:
                    logger.warning("DashScope 参考图读取失败: %s (%s)", path, exc)
                    unreadable.append(path.name)
            if unreadable:
                raise ImageCapabilityError(
                    "image_reference_images_unreadable", model=self._model, names=", ".join(unreadable)
                )
            content.extend({"image": uri} for uri in data_uris)
        content.append({"text": request.prompt})
        return content


class DashScopeWanImageBackend(DashScopeImageBackend):
    """Wan arbitrary-pixel requests, using standard pixel budgets whenever references exist."""

    def _resolve_size(self, request: ImageGenerationRequest, has_refs: bool) -> str:
        explicit = (request.image_size or "").strip()
        aspect = request.aspect_ratio
        # Model resolution options come from entries; referenced requests never exceed standard budgets.
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
    """Qwen editing requests with both dimensions capped at 2048."""

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
