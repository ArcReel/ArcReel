"""AgnesImageBackend: synchronous single-step OpenAI-compatible Agnes image generation.

Use apihub ``/images/generations``: one POST returns URLs/base64, immediately persisted. T2I/I2I share
endpoints, sending references in bodies. 2.1 computes exact aspect_size dimensions under ADR0011 and
explicitly sends ``size: WxH``; 2.5 uses ``size`` tiers + ``ratio`` instead.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import ClassVar

from arcreel_market_core.aspect_size import IMAGE_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from lib.backends.agnes_shared import agnes_base_url, agnes_headers, resolve_agnes_api_key
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
from lib.backends.providers import PROVIDER_AGNES
from lib.billing.metering import Usage
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "agnes-image-2.5-flash"

_IMAGE_ENDPOINT = "/images/generations"

# Dimensions divisible by 8, long edges capped at conservative 2048 for common flash tiers. Without image_size,
# use 2K short edges, becoming portrait 1152x2048 after long-edge capping.
_ROUND_TO = 8
_MAX_LONG_EDGE = 2048
_DEFAULT_SHORT = 1440

# Allowlisted scalar logs only, prompt lengths/image counts, never base64/URLs.
_SAFE_LOG_KEYS = ("model", "size", "n")


def _extract_first_str(payload: object, key: str) -> str | None:
    """First nonempty compatible ``data[].<key>`` string, shared by url / b64_json; otherwise None."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                value = item.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def _safe_body_for_log(body: dict) -> dict:
    """Safe logs: allowlisted scalars, prompt lengths, image counts only."""
    view: dict = {key: body[key] for key in _SAFE_LOG_KEYS if key in body}
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        view["prompt_len"] = len(prompt)
    images = body.get("image")
    if isinstance(images, list) and images:
        view["image"] = f"<{len(images)} ref>"
    return view


class AgnesImageBackend:
    """Agnes pixel-size requests metered by short-edge tiers."""

    DEFAULT_MODEL = "agnes-image-2.1-flash"
    SENDS_RATIO = False

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    # Agnes never clips reference counts, sending all images.
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = None

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120.0,
    ) -> None:
        self._api_key = resolve_agnes_api_key(api_key)
        self._base_url = agnes_base_url(base_url)
        self._model = model or self.DEFAULT_MODEL
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_AGNES

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    @classmethod
    def _request_size(cls, image_size: str | None, aspect_ratio: str) -> str:
        """Pixel endpoints send aspect-derived dimensions."""
        short = resolution_to_short_edge(
            image_size or None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=_DEFAULT_SHORT
        )
        width, height = aspect_size(aspect_ratio, short, round_to=_ROUND_TO, max_long_edge=_MAX_LONG_EDGE)
        return f"{width}x{height}"

    @staticmethod
    def _billing_resolution(size: str) -> str:
        """Pixel endpoints bill output-resolution tiers, converting sent dimensions through short edges.

        Dimensions derive from ``IMAGE_TIER_SHORT_EDGE`` and cap long edges at 2048. Short edges within
        1K's boundary count as 1K; others as 2K.
        """
        width, height = (int(edge) for edge in size.split("x"))
        return "1K" if min(width, height) <= IMAGE_TIER_SHORT_EDGE["1K"] else "2K"

    @classmethod
    def estimate_usage(cls, facts: ImageRequestFacts) -> Usage:
        size = cls._request_size(facts.image_size, facts.aspect_ratio)
        return per_image_usage(
            output_images=1,
            input_images=facts.reference_count,
            dimensions={"resolution": cls._billing_resolution(size)},
        )

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        # No orchestration retries: isolate nonidempotent billed creation and idempotent downloads into
        # _submit / _download_result scopes, avoiding retrieval failures repeating billed POSTs.
        # Omit response_format, rejected by upstream litellm as UnsupportedParamsError.
        # Default responses contain url and b64_json; _persist_image prefers URLs.
        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "n": 1,
        }
        payload["size"] = self._request_size(request.image_size, request.aspect_ratio)
        if self.SENDS_RATIO:
            payload["ratio"] = request.aspect_ratio
        if request.reference_images:
            # Send I2I data-URI arrays in image. Reads/base64 encoding, potentially megabytes,
            # run off-thread to preserve event-loop responsiveness.
            payload["image"] = await asyncio.to_thread(self._build_reference_images, request)

        data = await self._submit(payload)
        image_uri = await self._persist_image(data, request.output_path)
        logger.info("Agnes 图片生成完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_AGNES,
            model=self._model,
            image_uri=image_uri,
            usage=per_image_usage(
                output_images=1,
                input_images=len(payload.get("image", [])),
                dimensions={"resolution": self._billing_resolution(payload["size"])},
            ),
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _submit(self, payload: dict) -> dict:
        """Nonidempotent billed image POST, returning parsed bodies.

        Retry this method only, not downloads, preventing retrieval failures from recreating/billing images.
        submit_post terminally converts ambiguous transport to AmbiguousSubmitError. >=400 logs bodies/raises
        HTTPStatusError for should_retry_submit status routing.
        """
        logger.info(
            "调用 %s 图片 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(_safe_body_for_log(payload)),
        )
        async with artifact_http_client(timeout=self._http_timeout) as client:
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_IMAGE_ENDPOINT}",
                    json=payload,
                    headers=agnes_headers(self._api_key),
                ),
                provider=PROVIDER_AGNES,
            )
            return resp.json()

    def _build_reference_images(self, request: ImageGenerationRequest) -> list[str]:
        """Build I2I base64 data-URI lists.

        Any missing/unreadable reference fails loudly with image_reference_images_unreadable, showing
        unused images instead of silently dropping them while still charging.
        """
        uris: list[str] = []
        for idx, ref in enumerate(request.reference_images, start=1):
            # Empty paths lack displayable filenames; use ordinals to keep Chinese placeholders out of other locales.
            path = Path(ref.path) if ref.path else None
            if path is None or not path.is_file():
                raise ImageCapabilityError(
                    "image_reference_images_unreadable",
                    model=self._model,
                    names=path.name if path and path.name else f"#{idx}",
                )
            try:
                uris.append(image_to_base64_data_uri(path))
            except OSError as exc:
                logger.warning("Agnes 参考图读取失败: %s (%s)", path, exc)
                raise ImageCapabilityError(
                    "image_reference_images_unreadable", model=self._model, names=path.name
                ) from exc
        return uris

    async def _persist_image(self, data: dict, output_path: Path) -> str | None:
        """Persist images/generations locally, returning remote URLs or None for base64.

        Prefer URLs with immediate downloads. Missing/failed URLs fall back to base64 in the same response,
        preserving billed successful generation despite retrieval failure. Both empty means error.
        """
        url = _extract_first_str(data, "url")
        b64 = _extract_first_str(data, "b64_json")
        if url:
            try:
                await self._download_result(url, output_path)
                return url
            except Exception:
                if not b64:
                    raise
                logger.warning("Agnes 结果 URL 下载失败，改用响应内 b64_json 落盘")

        if b64:
            await _write_base64_image(b64, output_path)
            return None

        # Full bodies may include prompts/signed URLs; match request-log redaction: log keys and
        # data counts only, never full bodies. Exclude bodies from errors too, preventing 503/timeout substrings
        # from misleading default should_retry, consistent with status-based retry policy.
        data_items = data.get("data")
        logger.error(
            "Agnes 图像响应缺少 url/b64_json: keys=%s data_count=%s",
            sorted(str(key) for key in data),
            len(data_items) if isinstance(data_items, list) else None,
        )
        raise RuntimeError("Agnes 图像响应缺少 url/b64_json")

    async def _download_result(self, url: str, output_path: Path) -> None:
        """Download issued image URLs via idempotent GET under shared artifact budgets.

        Retry transients here, never restart nonidempotent generation POST. Shared predicates also retry
        403/404 because just-issued URLs may not have propagated in object storage. Other 4xx fail deterministically.
        """
        await with_artifact_retry(lambda: download_image_to_path(url, output_path), label="Agnes 图像")


async def _write_base64_image(b64: str, output_path: Path) -> None:
    """Decode/persist base64 images in threads.

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


class AgnesTierImageBackend(AgnesImageBackend):
    """Tier size + ratio requests with tier-based metering."""

    DEFAULT_MODEL = DEFAULT_MODEL
    SENDS_RATIO = True

    @classmethod
    def _request_size(cls, image_size: str | None, aspect_ratio: str) -> str:
        return (image_size or "1K").upper()

    @staticmethod
    def _billing_resolution(size: str) -> str:
        return size


AGNES_IMAGE_BACKENDS = {
    "agnes-image-pixels": AgnesImageBackend,
    "agnes-image-tier": AgnesTierImageBackend,
}
