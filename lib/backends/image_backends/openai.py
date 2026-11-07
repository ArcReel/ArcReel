"""OpenAIImageBackend: OpenAI image-generation backend."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

import openai

from arcreel_catalog_core import ImageFacts
from arcreel_market_core.aspect_size import IMAGE_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from arcreel_market_core.video_backend_contract import IMAGE_MIME_TYPES
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
    per_image_usage,
    save_image_from_response_item,
)
from lib.backends.openai_shared import (
    OPENAI_IMAGE_QUALITY_MAP as _QUALITY_MAP,
)
from lib.backends.openai_shared import (
    OPENAI_RETRYABLE_ERRORS,
    create_openai_client,
)
from lib.backends.providers import PROVIDER_OPENAI
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-image-2"
_MAX_REFERENCE_IMAGES = 16
ImageBackendMode = Literal["both", "generations_only", "edits_only"]

# gpt-image-2 / gpt-image-2-2026-04-21 arbitrary WxH, dimensions divisible by 16, ratios 1:3~3:1;
# stable ≤2560x1440, experimental approximately 3840x2160, maximum 3840x2160 (4K).
_GPT_IMAGE_MAX_LONG_EDGE = 3840
_GPT_IMAGE_STABLE_LONG_EDGE = 2560
_GPT_IMAGE_MAX_RATIO = 3.0

# Case-insensitive tier→quality, matching resolution_to_short_edge. User tiers may be
# 2k/4K with whitespace; without normalization size succeeds but quality silently disappears. Custom WxH → None.
_QUALITY_MAP_CI = {k.lower(): v for k, v in _QUALITY_MAP.items()}


def _quality_for(image_size: str | None) -> str | None:
    return _QUALITY_MAP_CI.get(image_size.strip().lower()) if image_size else None


def _resolve_openai_params(
    image_size: str | None,
    aspect_ratio: str,
) -> dict[str, str]:
    """Compute {size, quality}, prioritizing ratio over resolution.

    aspect_ratio always determines ratio; image_size tiers/custom WxH/None determine short-edge resolution
    only. Strip custom ratios by using min dimensions. None image_size defaults to 720P short edges,
    still sending exact-ratio size, never SDK defaults that would let relays replace project ratios.
    """
    short = resolution_to_short_edge(image_size, tier_map=IMAGE_TIER_SHORT_EDGE)
    w, h = aspect_size(
        aspect_ratio,
        short,
        round_to=16,
        max_long_edge=_GPT_IMAGE_MAX_LONG_EDGE,
        max_ratio=_GPT_IMAGE_MAX_RATIO,
    )
    if max(w, h) > _GPT_IMAGE_STABLE_LONG_EDGE:
        logger.warning(
            "OpenAI image: 尺寸 %dx%d 长边超过稳定区 %d，进入实验性高分辨率区间",
            w,
            h,
            _GPT_IMAGE_STABLE_LONG_EDGE,
        )
    params: dict[str, str] = {"size": f"{w}x{h}"}
    quality = _quality_for(image_size)
    if quality:
        params["quality"] = quality
    return params


def _output_token_bases(entry: CatalogEntry | None) -> Mapping[str, int]:
    """Entry output-token estimation bases by quality tier, empty for unmatched/nonimage/unlisted entries."""
    if entry is None or not isinstance(entry.facts, ImageFacts):
        return {}
    return entry.facts.output_token_bases


def estimate_output_tokens(size: str, quality: str, bases: Mapping[str, int]) -> int | None:
    """Estimate one image's output tokens from size/quality/entry bases; ``None`` for missing tiers or non-widthxheight sizes.

    Follow official Calculating costs formulas, with entry-provided quality bases: short-edge tile count
    = base ÷ aspect ratio, round nearest with ties to even; tiles = base × short-edge tiles;
    tokens = ⌈tiles × (2,000,000 + width × height) ÷ 4,000,000⌉.
    https://developers.openai.com/api/docs/guides/image-generation#calculating-costs
    """
    base = bases.get(quality)
    width_text, sep, height_text = size.partition("x")
    if base is None or not sep or not width_text.isdigit() or not height_text.isdigit():
        return None
    width, height = int(width_text), int(height_text)
    if width <= 0 or height <= 0:
        return None
    scaled = base / (max(width, height) / min(width, height))
    floor = math.floor(scaled)
    short_blocks = floor + floor % 2 if scaled - floor == 0.5 else math.floor(scaled + 0.5)
    return math.ceil(base * short_blocks * (2_000_000 + width * height) / 4_000_000)


def _token_usage(
    *,
    text_input: int | None,
    image_input: int | None,
    image_output: int | None,
    text_output: int | None,
    dimensions: dict[str, object],
) -> Usage:
    """Token usage separates prompt text, reference-image input, and output images, omitting missing items.

    Images API uses no caching: official cache prices apply only to Responses. Omit cache reads.
    """
    quantities = {
        meter: value
        for meter, value in (
            ("uncached_input_tokens", text_input),
            ("uncached_image_input_tokens", image_input),
            ("image_output_tokens", image_output),
            ("output_tokens", text_output),
        )
        if value is not None
    }
    return Usage(quantities=quantities, dimensions=dimensions)


def _dimensions(params: dict[str, str]) -> dict[str, object]:
    """Use sent ``size`` / ``quality`` as selection dimensions; omit unsent ``quality``."""
    dims: dict[str, object] = {"resolution": params["size"]}
    if "quality" in params:
        dims["quality"] = params["quality"]
    return dims


class OpenAIImageBackend:
    """OpenAI image backend supporting T2I / I2I / both by mode."""

    #: Built-ins default to ``both``; custom endpoints narrow by mode, whose declarations supply channel facts.
    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = _MAX_REFERENCE_IMAGES

    _MODE_TO_CAPS: ClassVar[dict[str, set[ImageCapability]]] = {
        "both": {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE},
        "generations_only": {ImageCapability.TEXT_TO_IMAGE},
        "edits_only": {ImageCapability.IMAGE_TO_IMAGE},
    }

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
        mode: ImageBackendMode = "both",
    ):
        self._client = create_openai_client(api_key=api_key, base_url=base_url, transport_layer=transport_layer)
        self._model = model or DEFAULT_MODEL
        self._capabilities = set(self._MODE_TO_CAPS[mode])

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        """Estimate output tokens through official formulas/entry quality bases; omit unlisted request quality tiers.

        Request tier mappings determine quality (:func:`_resolve_openai_params`); bases come from model
        ``facts.entry``, never model-name checks. Prompt tokens estimate zero because fee pages/batches lack
        prompts, underestimating this part. Images API text output/cache never occur, so record zero.
        Image input tokens cannot be estimated from facts: zero without references, omitted with references.
        """
        params = _resolve_openai_params(facts.image_size, facts.aspect_ratio)
        quality = params.get("quality")
        output = (
            estimate_output_tokens(params["size"], quality, _output_token_bases(facts.entry))
            if quality is not None
            else None
        )
        usage = _token_usage(
            text_input=0,
            image_input=0 if facts.reference_count == 0 else None,
            image_output=output,
            text_output=0,
            dimensions=_dimensions(params),
        )
        return Usage(
            quantities={**usage.quantities, "cache_read_tokens": 0, "image_cache_read_tokens": 0},
            dimensions=usage.dimensions,
        )

    @with_retry_async(retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        has_refs = bool(request.reference_images)
        if has_refs and ImageCapability.IMAGE_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_i2i", model=self._model)
        if not has_refs and ImageCapability.TEXT_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_t2i", model=self._model)
        return await (self._generate_edit(request) if has_refs else self._generate_create(request))

    async def _generate_create(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        kwargs = {
            "model": self._model,
            "prompt": request.prompt,
            "n": 1,
        }
        params = _resolve_openai_params(request.image_size, request.aspect_ratio)
        kwargs.update(params)
        logger.info("调用 %s 图片 SDK (T2I) kwargs=%s", self.name, format_kwargs_for_log(kwargs))
        response = await self._client.images.generate(**kwargs)
        return await self._save_and_return(response, request, params)

    async def _generate_edit(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        refs = request.reference_images

        def _read_refs() -> list[tuple[str, bytes, str]]:
            """Read reference bytes in threads; passing handles to SDK multipart would read files on event loops."""
            files = []
            for ref in refs:
                ref_path = Path(ref.path)
                try:
                    data = ref_path.read_bytes()
                except FileNotFoundError:
                    logger.warning("参考图不存在，跳过: %s", ref_path)
                    continue
                files.append((ref_path.name, data, IMAGE_MIME_TYPES.get(ref_path.suffix.lower(), "image/png")))
            return files

        image_files = await asyncio.to_thread(_read_refs)
        if not image_files:
            # Reject all-unreadable references instead of T2I fallback: requested I2I without usable materials is an error.
            raise ImageCapabilityError(
                "image_endpoint_mismatch_no_i2i",
                model=self._model,
                detail="all reference images failed to open",
            )
        # I2I sends size/quality like T2I; omitted edit sizes use upstream ratios, losing project aspect_ratio.
        edit_kwargs: dict = {
            "model": self._model,
            "image": image_files,
            "prompt": request.prompt,
        }
        params = _resolve_openai_params(request.image_size, request.aspect_ratio)
        edit_kwargs.update(params)
        logger.info(
            "调用 %s 图片 SDK (I2I) kwargs=%s",
            self.name,
            format_kwargs_for_log({**edit_kwargs, "image": f"<{len(image_files)} files>"}),
        )
        try:
            response = await self._client.images.edit(**edit_kwargs)
        except openai.NotFoundError as exc:
            # Seedream references need Ark endpoints; warn only when edits is absent, retaining compatible relays with edits.
            if "seedream" in self._model.lower():
                raise ImageCapabilityError("image_seedream_edits_unavailable", model=self._model) from exc
            raise
        return await self._save_and_return(response, request, params)

    async def _save_and_return(
        self, response, request: ImageGenerationRequest, params: dict[str, str]
    ) -> ImageGenerationResult:
        data = getattr(response, "data", None) or []
        if not data:
            # Empty data often means moderation filters/gateway anomalies; give clear diagnostic errors.
            raise RuntimeError(
                f"OpenAI 图片生成响应 data 为空 (model={self._model})，可能触发内容安全过滤或上游服务异常"
            )
        await save_image_from_response_item(data[0], request.output_path)
        logger.info("OpenAI 图片生成完成: %s", request.output_path)

        img_in = img_out = txt_in = txt_out = None
        usage = getattr(response, "usage", None)
        if usage is not None:
            try:
                in_details = getattr(usage, "input_tokens_details", None)
                # Require input splits with image_tokens / text_tokens at least partly present, otherwise None,
                # avoiding missing input charges when partial fields are absent.
                in_image = getattr(in_details, "image_tokens", None) if in_details is not None else None
                in_text = getattr(in_details, "text_tokens", None) if in_details is not None else None
                if in_image is not None or in_text is not None:
                    img_in = in_image
                    txt_in = in_text
                    out_details = getattr(usage, "output_tokens_details", None)
                    if out_details is not None:
                        img_out = getattr(out_details, "image_tokens", None)
                        txt_out = getattr(out_details, "text_tokens", None)
                    if img_out is None:
                        # Some models expose output_tokens only at top level; GPT Image outputs are mostly image tokens.
                        img_out = getattr(usage, "output_tokens", None)
                    # Input splits without any output usage are incomplete: withdraw and treat usage as unavailable.
                    if img_out is None and txt_out is None:
                        img_in = txt_in = None
            except Exception:
                logger.warning("OpenAI image usage 解析失败", exc_info=True)
                img_in = img_out = txt_in = txt_out = None

        # With usage, report tokens, not reference counts already included in image input. Without usage, output counts only;
        # token price tables mark such calls unpriced, never account estimated values.
        usage = (
            _token_usage(
                text_input=txt_in,
                image_input=img_in,
                image_output=img_out,
                text_output=txt_out,
                dimensions=_dimensions(params),
            )
            if img_out is not None or txt_out is not None
            else per_image_usage(output_images=len(data), input_images=None, dimensions=_dimensions(params))
        )
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_OPENAI,
            model=self._model,
            usage=usage,
            image_input_tokens=img_in,
            image_output_tokens=img_out,
            text_input_tokens=txt_in,
            text_output_tokens=txt_out,
        )
