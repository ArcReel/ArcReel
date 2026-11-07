"""OpenRouterImageBackend: unified OpenRouter image-generation backend.

OpenRouter image APIs differ from official OpenAI APIs: no /images/edits; T2I and
I2I share the JSON endpoint ``POST {base_url}/images``. References enter as data URIs in
``input_references``, returning ``data[].b64_json``. Therefore this backend avoids the OpenAI SDK,
uses MiniMax-style raw httpx requests, and decodes/persists normalized PNGs in a noninterruptible segment.

Model capabilities come from ``supported_parameters`` in ``GET {base_url}/images/models`` (cached per backend
instance: successful results 1h, failures 60s). The reference limit (``input_references``) passes through :meth:`reference_image_limit`
to orchestration for trimming before numbering; undeclared parameters (``n`` / ``seed``) are omitted. Size parameters
adapt to declarations (OpenRouter rejects simultaneous ``size`` and ``resolution`` with 400; the two mutually
exclusive parameter sets require selecting one from declarations):

- Declared ``resolution`` (gemini / seedream / qwen / grok / krea families, even with ``size`` also declared) →
  ``aspect_ratio`` + ``resolution`` (omit size). Map tier short edges to the smallest declared tier ≥ requested size; omit if unmappable.
- No ``resolution``, but ``size`` or ``aspect_ratio`` declared (gpt-image / recraft / mai families) → ``size``
  (declared enum values only, otherwise synthesize OpenAI-style WxH) + ``aspect_ratio``, adding ``quality`` if declared.
- Unlisted model or failed catalog lookup → send only ``size`` (like OpenAI-compatible gateways), keeping generation
  available despite lookup failures.
- Listed model declaring none of size / aspect_ratio / resolution (such as wrapper models) → omit all size parameters,
  trusting model defaults.

Usage reports output/reference image counts only, without parsing response ``usage.cost``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import httpx

from arcreel_market_core.aspect_size import (
    IMAGE_TIER_SHORT_EDGE,
    aspect_size,
    parse_aspect_ratio,
    resolution_to_short_edge,
)
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import should_retry_submit, submit_post, with_artifact_retry
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageOutputFormatError,
    ImageOutputMissingError,
    download_image_to_path,
    image_to_base64_data_uri,
    per_image_usage,
)
from lib.backends.openai_shared import OPENAI_IMAGE_QUALITY_MAP as _QUALITY_MAP
from lib.backends.providers import PROVIDER_OPENROUTER
from lib.backends.transport_layer import TransportLayer
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.image_utils import convert_image_bytes_to_png
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-image-2"
_MAX_REFERENCE_IMAGES = 16

#: Generation and model-catalog paths relative to base_url; path overrides (docs/adr/0101) recognize these defaults.
IMAGES_PATH = "/images"
MODELS_PATH = "/images/models"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: Output formats that can be converted to PNG after persistence.
_RASTER_OUTPUT_FORMATS = frozenset({"png", "jpeg", "jpg", "webp"})

#: Generation can take 90s+, requiring much longer timeouts than ordinary requests.
_HTTP_TIMEOUT = httpx.Timeout(300.0, connect=15.0)
#: Catalog lookup is lightweight metadata: tighter timeouts; failures affect parameter refinement only, not generation.
_MODELS_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# Case-insensitive tier → quality, matching OpenAIImageBackend: custom tiers may use
# "2k"/"4K "; without normalization size succeeds but quality silently disappears. Custom WxH (no tier) → None.
_QUALITY_MAP_CI = {k.lower(): v for k, v in _QUALITY_MAP.items()}

# Cache catalog results per backend instance, constructed for provider configuration (base_url, credentials, path overrides).
# Configuration changes invalidate the entire backend cache and its catalog results, preventing reuse across configurations.
# Successful results (including unlisted models) last 1h; failures briefly cache as unlisted to avoid repeated lookups/stack traces.
_MODEL_PARAMS_TTL_SECONDS = 3600.0
_MODEL_PARAMS_FAILURE_TTL_SECONDS = 60.0


def _quality_for(image_size: str | None) -> str | None:
    return _QUALITY_MAP_CI.get(image_size.strip().lower()) if image_size else None


def _enum_values(supported: dict, name: str) -> list[str]:
    """Get declared enum values ({"type": "enum", "values": [...]}), or [] for missing / malformed declarations."""
    spec = supported.get(name)
    values = spec.get("values") if isinstance(spec, dict) else None
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


def _range_bound(supported: dict, name: str, bound: str) -> int | None:
    """Get one declared range bound ({"type": "range", "min": m, "max": n}, ``bound`` is min / max); None if absent."""
    spec = supported.get(name)
    if not isinstance(spec, dict):
        return None
    value = spec.get(bound)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _declared_reference_max(supported: dict | None) -> int | None:
    """Catalog reference maximum: ``0`` when a listed model omits ``input_references`` (no references);
    ``None`` for unavailable catalogs, unlisted models, or declarations without a maximum (unknown)."""
    if supported is None:
        return None
    if "input_references" not in supported:
        return 0
    value = _range_bound(supported, "input_references", "max")
    return None if value is None else max(value, 0)


def _effective_reference_limit(supported: dict | None) -> int:
    """Effective trimming count: cap declared maxima at the endpoint limit of 16; use 16 without a positive declared maximum."""
    declared = _declared_reference_max(supported)
    return min(declared, _MAX_REFERENCE_IMAGES) if declared else _MAX_REFERENCE_IMAGES


def _resolution_px(value: str) -> int | None:
    """Convert resolution enum values to short-edge pixels ("512" → 512, "1K" / "1.5K" / "2K" → 1024 / 1536 / 2048)."""
    text = value.strip().upper()
    scale = 1
    if text.endswith("K"):
        text, scale = text[:-1], 1024
    try:
        px = round(float(text) * scale)
    except ValueError:
        return None
    return px if px > 0 else None


def _pick_resolution(values: list[str], short_edge: int | None) -> str | None:
    """Tier short edge → smallest declared tier ≥ requested size (no downgrade); use the maximum if all tiers are smaller."""
    if short_edge is None:
        return None
    candidates = sorted(px for v in values if (px := _resolution_px(v)) is not None)
    if not candidates:
        return None
    for px in candidates:
        if px >= short_edge:
            for v in values:
                if _resolution_px(v) == px:
                    return v
    for v in values:
        if _resolution_px(v) == candidates[-1]:
            return v
    return None


def _pick_aspect_ratio(values: list[str], aspect_ratio: str) -> str | None:
    """Project ratio → declared enum: prefer exact case-insensitive matches, otherwise the numerically closest ratio."""
    candidates = [v for v in values if v.lower() != "auto"]
    lowered = {v.lower(): v for v in candidates}
    if aspect_ratio.lower() in lowered:
        return lowered[aspect_ratio.lower()]
    target_w, target_h = parse_aspect_ratio(aspect_ratio)
    target = target_w / target_h
    best: tuple[float, str] | None = None
    for v in candidates:
        try:
            w, h = parse_aspect_ratio(v)
        except ValueError:
            continue
        score = abs(w / h - target)
        if best is None or score < best[0]:
            best = (score, v)
    return best[1] if best else None


def _parse_size(value: str) -> tuple[int, int] | None:
    """Parse size enum values into (width, height); non-WxH values such as "auto" → None."""
    w, sep, h = value.strip().lower().partition("x")
    if not sep or not w.isdigit() or not h.isdigit() or int(w) <= 0 or int(h) <= 0:
        return None
    return int(w), int(h)


def _pick_size(values: list[str], aspect_ratio: str, short_edge: int) -> str | None:
    """Select declared WxH: prefer the closest ratio, then the smallest short edge ≥ requested size among equal ratios;
    use the maximum if all are smaller. No parseable WxH enum values → None."""
    parsed = [(v, dims) for v in values if (dims := _parse_size(v)) is not None]
    if not parsed:
        return None
    target_w, target_h = parse_aspect_ratio(aspect_ratio)
    target = target_w / target_h
    best_score = min(abs(w / h - target) for _, (w, h) in parsed)
    same_ratio = sorted(
        ((min(w, h), v) for v, (w, h) in parsed if abs(abs(w / h - target) - best_score) < 1e-9),
    )
    for edge, v in same_ratio:
        if edge >= short_edge:
            return v
    return same_ratio[-1][1]


def _resolve_model_params(supported: dict | None, image_size: str | None, aspect_ratio: str) -> dict[str, str]:
    """Send size parameters adaptively from declarations, selecting only one mutually exclusive channel (size+resolution always returns 400)."""
    quality = _quality_for(image_size)
    if supported is None:
        # Unlisted model / unavailable catalog: OpenAI-style size only, never undeclared parameters such as quality.
        short = resolution_to_short_edge(image_size, tier_map=IMAGE_TIER_SHORT_EDGE)
        w, h = aspect_size(aspect_ratio, short, round_to=16)
        return {"size": f"{w}x{h}"}

    size_values = _enum_values(supported, "size")
    resolution_values = _enum_values(supported, "resolution")
    ratio_values = _enum_values(supported, "aspect_ratio")
    quality_values = _enum_values(supported, "quality")

    params: dict[str, str] = {}
    ratio = _pick_aspect_ratio(ratio_values, aspect_ratio) if ratio_values else None
    # Compute size from the ratio actually sent: use the selected enum ratio, falling back to the requested ratio only without a match.
    # Otherwise size could use 2:3 while aspect_ratio says 9:16, contradicting each other with unpredictable model behavior.
    effective_ratio = ratio or aspect_ratio
    short = resolution_to_short_edge(image_size, tier_map=IMAGE_TIER_SHORT_EDGE)
    if resolution_values:
        # Declared resolution takes this branch even when size is declared: mutually exclusive, so omit size. Omit ratio / resolution
        # individually when unmappable (tier mismatch or extreme ratios beyond enums), avoiding conflicting parameters rejected with 400.
        resolution = _pick_resolution(resolution_values, short)
        if resolution:
            params["resolution"] = resolution
        if ratio:
            params["aspect_ratio"] = ratio
    elif size_values or ratio:
        # Declared size or aspect_ratio alone: WxH provides resolution and ratio locks proportions. With size enums, send enum values only:
        # choose closest WxH, or auto when it is the only option; synthesize exact OpenAI-style WxH only without declared size.
        size = _pick_size(size_values, effective_ratio, short)
        if size is None and size_values:
            size = next((v for v in size_values if v.strip().lower() == "auto"), None)
        if size is None and not size_values:
            w, h = aspect_size(effective_ratio, short, round_to=16)
            size = f"{w}x{h}"
        if size is not None:
            params["size"] = size
        if ratio:
            params["aspect_ratio"] = ratio
    # Listed model without any of the three declarations: omit all size parameters and trust model defaults.
    if quality and quality_values and quality.lower() in {v.lower() for v in quality_values}:
        params["quality"] = quality
    return params


class OpenRouterImageBackend:
    """Unified OpenRouter image backend: T2I and I2I both hit ``POST {base_url}/images``."""

    # Endpoint accepts at most 16 images; narrow further using declared ``input_references`` at runtime (see :meth:`reference_image_limit`).
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = _MAX_REFERENCE_IMAGES

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._api_key = api_key or ""
        self._model = model or DEFAULT_MODEL
        # OpenRouter base_url already includes /api/v1; do not append /v1 through ensure_openai_base_url.
        self._base_url = (str(base_url).strip().rstrip("/") if base_url else "") or DEFAULT_BASE_URL
        # Transport rewrites request paths through overrides (docs/adr/0101); both generation and catalog requests pass through it.
        self._transport_layer = transport_layer
        # (clock expiry, supported_parameters); None means never queried. Serialize concurrent first queries with a lock
        # to prevent a late failure from overwriting an earlier success.
        self._clock = clock
        self._model_params: tuple[float, dict | None] | None = None
        self._model_params_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return PROVIDER_OPENROUTER

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def reference_image_limit(self) -> int:
        """Maximum references per request for this model: declared ``input_references`` maximum, capped at 16.

        Use the endpoint limit of 16 for unavailable catalogs, unlisted models, or no usable declared maximum:
        ``generate`` rejects models without declared reference support as lacking image-to-image support (docs/adr/0015).
        """
        return _effective_reference_limit(await self._fetch_supported_params())

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        supported = await self._fetch_supported_params()
        refs = self._fit_references(request.reference_images, supported)

        input_references = await asyncio.to_thread(self._encode_references, refs)
        payload: dict = {"model": self._model, "prompt": request.prompt}
        # Omit undeclared parameters (n / seed) for listed models because they are rejected; include normally for unlisted models.
        if supported is None or "n" in supported:
            payload["n"] = 1
        payload.update(_resolve_model_params(supported, request.image_size, request.aspect_ratio))
        if request.seed is not None and (supported is None or "seed" in supported):
            payload["seed"] = request.seed
        # Artifact paths always end in .png: request png when declared to avoid subsequent conversion. If declared formats contain
        # no raster (such as SVG only), conversion to PNG is impossible: reject before submitting this billed request.
        output_formats = [v.lower() for v in _enum_values(supported, "output_format")] if supported else []
        if "png" in output_formats:
            payload["output_format"] = "png"
        elif output_formats and not _RASTER_OUTPUT_FORMATS.intersection(output_formats):
            raise ImageOutputFormatError(model=self._model, media_type="/".join(output_formats))
        if input_references:
            payload["input_references"] = input_references

        body = await self._submit(payload)
        await self._persist_image(body, request.output_path)
        logger.info("OpenRouter 图片生成完成: %s", request.output_path)
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_OPENROUTER,
            model=self._model,
            # Usage contract (docs/adr/0100): report output and actually sent reference image counts.
            usage=per_image_usage(output_images=1, input_images=len(input_references)),
        )

    def _fit_references(self, refs: list, supported: dict | None) -> list:
        """Validate references against catalog declarations: reject unsupported, required-but-missing, or excessive references without truncating.

        Orchestration already trims using :meth:`reference_image_limit` and numbers references as “图N” in prompts. Excess here
        indicates inconsistent catalog reads (such as a failed lookup allowing 16 numbered references, followed by a smaller limit
        before submission). Truncation would leave prompt references to unsent images while still charging, so refuse submission
        and let regeneration trim and renumber using the latest limit.
        """
        if supported is not None:
            if refs and _declared_reference_max(supported) == 0:
                # Listed model omits references or declares maximum 0: no provider accepts references.
                raise ImageCapabilityError(
                    "image_endpoint_mismatch_no_i2i",
                    model=self._model,
                    detail=f"model {self._model} declares no input_references",
                )
            declared_min = _range_bound(supported, "input_references", "min") or 0
            if not refs and declared_min > 0:
                raise ImageCapabilityError("image_endpoint_mismatch_no_t2i", model=self._model)
        limit = _effective_reference_limit(supported)
        if len(refs) > limit:
            raise ImageCapabilityError(
                "image_reference_images_exceeded", model=self._model, limit=limit, count=len(refs)
            )
        return refs

    async def _fetch_supported_params(self) -> dict | None:
        """Fetch model supported_parameters (cached per instance); None for unavailable catalogs or unlisted models.

        Network errors and non-2xx responses only affect parameter refinement: generation continues with size fallback despite lookup failures.
        """
        cached = self._model_params
        if cached and self._clock() < cached[0]:
            return cached[1]
        async with self._model_params_lock:
            cached = self._model_params
            if cached and self._clock() < cached[0]:
                return cached[1]
            supported, ttl = await self._query_supported_params()
            self._model_params = (self._clock() + ttl, supported)
            return supported

    async def _query_supported_params(self) -> tuple[dict | None, float]:
        """Query the model catalog once, returning (supported_parameters, result cache lifetime)."""
        try:
            async with artifact_http_client(timeout=_MODELS_TIMEOUT, transport_layer=self._transport_layer) as client:
                resp = await client.get(
                    f"{self._base_url}{MODELS_PATH}",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
                items = (resp.json() or {}).get("data") or []
        except Exception:
            logger.warning(
                "OpenRouter 模型目录查询失败，%ds 内按未收录处理: model=%s",
                int(_MODEL_PARAMS_FAILURE_TTL_SECONDS),
                self._model,
                exc_info=True,
            )
            return None, _MODEL_PARAMS_FAILURE_TTL_SECONDS
        for item in items:
            if isinstance(item, dict) and item.get("id") == self._model:
                raw = item.get("supported_parameters")
                return (raw if isinstance(raw, dict) else None), _MODEL_PARAMS_TTL_SECONDS
        return None, _MODEL_PARAMS_TTL_SECONDS

    def _encode_references(self, refs) -> list[dict]:
        """Encode reference images as data URIs; fail loudly if any cannot be read.

        Prompts identify references as “图N” by array position. Skipping missing images shifts later references and misidentifies
        them while generation still charges. Collect all unreadable images, then raise image_reference_images_unreadable together.
        """
        encoded: list[dict] = []
        unreadable: list[str] = []
        # names enter multilingual error templates: empty paths use ordinal #N instead of placeholders leaking into other locales.
        for idx, ref in enumerate(refs, start=1):
            path = Path(ref.path) if ref.path else None
            if path is None or not path.is_file():
                unreadable.append(path.name if (path and path.name) else f"#{idx}")
                continue
            try:
                encoded.append({"type": "image_url", "image_url": {"url": image_to_base64_data_uri(path)}})
            except OSError as exc:
                logger.warning("OpenRouter 参考图读取失败: %s (%s)", path, exc)
                unreadable.append(path.name or f"#{idx}")
        if unreadable:
            raise ImageCapabilityError(
                "image_reference_images_unreadable", model=self._model, names=", ".join(unreadable)
            )
        return encoded

    @with_retry_async(retry_if=should_retry_submit)
    async def _submit(self, payload: dict) -> Any:
        """Single-step image-generation POST (nonidempotent creation + billing), returning parsed response bodies.

        Retry strictly within this method, excluding persistence, so persistence failures cannot repeat the whole workflow and
        duplicate creation/billing. submit_post converts ambiguous transport errors to terminal AmbiguousSubmitError to avoid
        duplicate billing; >=400 raises HTTPStatusError for should_retry_submit to route by status.
        """
        log_view = {**payload, "prompt": payload.get("prompt", "")[:80]}
        logger.info("调用 %s 图片 API kwargs=%s", self.name, format_kwargs_for_log(log_view))
        async with artifact_http_client(timeout=_HTTP_TIMEOUT, transport_layer=self._transport_layer) as client:
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{IMAGES_PATH}",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                ),
                provider=PROVIDER_OPENROUTER,
            )
            return resp.json()

    async def _persist_image(self, body: Any, output_path: Path) -> None:
        items = body.get("data") if isinstance(body, dict) else None
        first = items[0] if isinstance(items, list) and items else None
        if not isinstance(first, dict) or not (first.get("b64_json") or first.get("url")):
            # Successful response without images usually means moderation filtering or upstream anomalies.
            raise ImageOutputMissingError(model=self._model)
        media_type = first.get("media_type") if isinstance(first.get("media_type"), str) else None
        b64 = first.get("b64_json")
        # Provider has returned and billed: persistence and PNG conversion finish in a noninterruptible segment so cancellation cannot lose paid artifacts
        # (docs/standards/backend.md, “The accounting segment after the provider returns is noninterruptible”).
        if b64:

            def _decode_and_save() -> None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(base64.b64decode(b64))
                self._ensure_png(output_path, media_type)

            await run_noninterruptible_sync(_decode_and_save)
            return
        # Retry retrieval failures only in downloads, never restart billed image-generation requests.
        await with_artifact_retry(lambda: download_image_to_path(first["url"], output_path), label="OpenRouter 图像")
        await run_noninterruptible_sync(self._ensure_png, output_path, media_type)

    def _ensure_png(self, output_path: Path, media_type: str | None) -> None:
        """Artifacts always use .png paths: convert JPEG / WebP and other rasters to PNG; reject unconvertible outputs (such as SVG) without leaving mislabeled artifacts."""
        content = output_path.read_bytes()
        if content.startswith(_PNG_SIGNATURE):
            return
        try:
            png = convert_image_bytes_to_png(content)
        except ValueError:
            # Remove unconvertible content: format mismatches the .png extension and downstream would misread it as PNG.
            output_path.unlink(
                missing_ok=True
            )  # hard-delete: temp -- just-written provider output in a staging path that cannot be converted
            raise ImageOutputFormatError(model=self._model, media_type=media_type or "unknown") from None
        logger.info("OpenRouter 返回 %s，已转为 PNG: %s", media_type or "非 PNG 图片", output_path)
        output_path.write_bytes(png)
