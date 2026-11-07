"""MiniMaxImageBackend: synchronous single-step MiniMax (Hailuo) image-01 generation.

Native /image_generation shares OpenAI-compatible bases. One POST returns URLs valid 24h or base64,
immediately persisted locally. T2I/I2I share bodies; subject_reference single faces preserve characters
across scenes. Compute width/height, 512–2048 and multiples of 8, from aspect_ratio + image_size,
prioritizing ratio over resolution.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import ClassVar

from arcreel_market_core.aspect_size import IMAGE_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import should_retry_submit, submit_post, with_artifact_retry
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
    download_image_to_path,
    image_to_base64_data_uri,
    per_image_usage,
)
from lib.backends.minimax_shared import (
    extract_image_base64,
    extract_image_url,
    minimax_failure_reason,
    minimax_headers,
    minimax_text_base_url,
    resolve_minimax_api_key,
    safe_body_for_log,
)
from lib.backends.providers import PROVIDER_MINIMAX
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "image-01"

_IMAGE_ENDPOINT = "/image_generation"

# image-01 dimensions [512, 2048], divisible by 8; most extreme representable ratio 2048/512 = 4:1.
_MIN_EDGE = 512
_MAX_EDGE = 2048
_ROUND_TO = 8
_MAX_RATIO = 4.0

# Default short edge without image_size, 2K tier, becomes portrait 1152*2048 under max_long_edge=2048.
_DEFAULT_SHORT = 1440

# image-01 subject_reference supports one face image only.
_SUBJECT_REF_LIMIT = 1


def _clamp_edge(value: int) -> int:
    """Clamp dimensions to [512, 2048]; both endpoints are divisible by 8, preserving alignment."""
    return max(_MIN_EDGE, min(_MAX_EDGE, value))


class MiniMaxImageBackend:
    """MiniMax image backend, synchronous single-step image_generation."""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = _SUBJECT_REF_LIMIT

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        self._base_url = minimax_text_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._transport_layer = transport_layer

    @property
    def name(self) -> str:
        return PROVIDER_MINIMAX

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        # Text-to-image and subject_reference image-to-image share endpoints.
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        """Estimate image counts only: uniform image-01 per-image pricing ignores sizes."""
        refs = facts.reference_count
        return per_image_usage(output_images=1, input_images=refs)

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        # No orchestration retries: isolate nonidempotent billed image submit and idempotent downloads into
        # _submit / _download_result retry scopes, preventing retrieval failures from repeating billed generation.
        width, height = self._resolve_dimensions(request)

        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "width": width,
            "height": height,
            "response_format": "url",
            "n": 1,
            # ArcReel prompts are already LLM-refined; disable optimization to retain intent.
            "prompt_optimizer": False,
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.reference_images:
            # Reference reads/base64, possibly megabytes, run off-thread to preserve event-loop responsiveness.
            # One to_thread call wraps the entire synchronous helper, minimizing scheduling overhead.
            payload["subject_reference"] = await asyncio.to_thread(self._build_subject_reference, request)

        data = await self._submit(payload)
        image_uri = await self._persist_image(data, request.output_path)
        logger.info("MiniMax 图片生成完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_MINIMAX,
            model=self._model,
            image_uri=image_uri,
            usage=per_image_usage(
                output_images=1,
                input_images=len(payload.get("subject_reference", [])),
                dimensions={"resolution": f"{width}x{height}"},
            ),
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _submit(self, payload: dict) -> dict:
        """Nonidempotent billed image-generation POST, returning parsed responses.

        Retry this method only, excluding downloads, so retrieval failures never restart image creation /
        billing. submit_post converts ambiguous transport into terminal AmbiguousSubmitError. >=400 logs
        bodies/raises HTTPStatusError, retaining status_code for central 413 lower-tier fallback;
        should_retry_submit routes by statuses.
        """
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
                    headers=minimax_headers(self._api_key),
                ),
                provider=PROVIDER_MINIMAX,
            )
            return resp.json()

    def _resolve_dimensions(self, request: ImageGenerationRequest) -> tuple[int, int]:
        """Compute (width, height), prioritizing ratio over resolution.

        aspect_ratio alone determines ratio; image_size tiers/custom width*height/None determine short-edge
        resolution only. Strip custom ratios by min dimensions. Clamp short edges to ≥ _MIN_EDGE before
        sizing: otherwise aspect_size may yield <512, and independent _clamp_edge would corrupt ratios,
        such as 16:9 becoming 512x512 square. Results align to 8, cap long edges at 2048, and independently
        clamp every edge to [512, 2048] as final protection.
        """
        short = max(
            _MIN_EDGE,
            resolution_to_short_edge(
                request.image_size or None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=_DEFAULT_SHORT
            ),
        )
        w, h = aspect_size(
            request.aspect_ratio, short, round_to=_ROUND_TO, max_long_edge=_MAX_EDGE, max_ratio=_MAX_RATIO
        )
        return _clamp_edge(w), _clamp_edge(h)

    def _build_subject_reference(self, request: ImageGenerationRequest) -> list[dict]:
        """Build single-face references; reject excessive/unreadable inputs instead of dropping images while still charging."""
        refs = request.reference_images
        if len(refs) > _SUBJECT_REF_LIMIT:
            raise ImageCapabilityError(
                "image_reference_images_exceeded",
                model=self._model,
                limit=self.CHANNEL_REFERENCE_LIMIT,
                count=len(refs),
            )
        ref = refs[0]
        # Empty paths lack filenames; identify as #1 to keep Chinese placeholders out of non-Chinese errors.
        path = Path(ref.path) if ref.path else None
        if path is None or not path.is_file():
            raise ImageCapabilityError(
                "image_reference_images_unreadable", model=self._model, names=path.name if path else "#1"
            )
        try:
            image_file = image_to_base64_data_uri(path)
        except OSError as exc:
            logger.warning("MiniMax 参考图读取失败: %s (%s)", path, exc)
            raise ImageCapabilityError("image_reference_images_unreadable", model=self._model, names=path.name) from exc
        return [{"type": "character", "image_file": image_file}]

    async def _persist_image(self, data: dict, output_path: Path) -> str | None:
        """Persist image_generation responses locally, returning remote URLs or None for base64.

        First check base_resp business failures, HTTP 200 with nonzero status_code. Prefer URLs, downloading
        immediately before 24-hour expiry; missing URLs fall back to base64. Both absent is an error.
        """
        reason = minimax_failure_reason(data)
        if reason:
            raise RuntimeError(reason)

        url = extract_image_url(data)
        if url:
            await self._download_result(url, output_path)
            return url

        b64 = extract_image_base64(data)
        if b64:
            await _write_base64_image(b64, output_path)
            return None

        # Log full responses for diagnosis but exclude bodies from exceptions, where 503/timeout substrings could
        # mislead default should_retry. Established policy retries by statuses, never strings.
        logger.error("MiniMax 图像响应缺少 image_urls/image_base64: %s", data)
        raise RuntimeError("MiniMax 图像响应缺少 image_urls/image_base64")

    async def _download_result(self, url: str, output_path: Path) -> None:
        """Download issued image URLs via idempotent GET under shared artifact budgets.

        Retry transient failures here only, never restart nonidempotent generation POST. Shared predicates
        retry 403/404 because just-issued artifact URLs may not have propagated in object storage.
        Other 4xx fail deterministically. Downloads have much larger budgets than generation because failed
        retrieval must not waste generation quota.
        """
        await with_artifact_retry(lambda: download_image_to_path(url, output_path), label="MiniMax 图像")


async def _write_base64_image(b64: str, output_path: Path) -> None:
    """Decode/persist base64 in threads, avoiding CPU-intensive decoding on event loops.

    Tolerate relay data URIs (``data:image/...;base64,<payload>``), stripping prefixes before decoding.
    """
    payload = b64
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]

    def _decode_and_save() -> None:
        image_bytes = base64.b64decode(payload)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)

    await run_noninterruptible_sync(_decode_and_save)
