"""KlingImageBackend: asynchronous native Kling images, direct JWT / relay Bearer modes.

Submit ``POST /v1/images/generations`` for ``data.task_id``; poll
``GET /v1/images/generations/{task_id}`` until ``task_status=succeed``; retrieve
``task_result.images[0].url`` valid 24h and download before expiry.

``KlingBackendBase`` shares authentication/base_url/submit-poll scaffolding with ``KlingVideoBackend``.
This class provides image paths, payloads, reference encoding, static capabilities, and ``api_model_name`` decoupling.

Two modes, matching ``KlingVideoBackend`` ``auth_mode`` precedent:
- ``auth_mode="jwt"`` built-ins take access_key + secret_key through ``KlingJWTManager``. Check every
  HTTP call and resign under 60s before expiry; async renders can outlive individual tokens.
- ``auth_mode="bearer"`` custom endpoints use static api_key + base_url, bypassing JWT management.

Registered image models:
- ``kling-image-o1``, default: text/image-to-image, 1–10 references with cross-image consistency, sent as ``image`` arrays.
- ``kling-v3-omni-image``: text/image-to-image and sets; registry alias with API name ``kling-v3-omni``.

Send ``api_model_name``, decoupling registry/API names for dual-modality models. Default to registry keys,
never blindly send keys or ``kling-v3-omni-image`` aliases would become invalid API names.

Registry resolution 1K/2K/4K drives UI/pricing, not payloads: official image fields model_name/prompt/image /
aspect_ratio/n omit resolution. Like KlingVideoBackend, send verified fields only, never guess external APIs.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import ClassVar

from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
    ReferenceImage,
    download_image_to_path,
    per_image_usage,
)
from lib.backends.kling_backend_base import KlingBackendBase
from lib.backends.kling_shared import (
    extract_kling_image_urls,
    image_to_base64,
)
from lib.backends.providers import PROVIDER_KLING
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kling-image-o1"

_IMAGE_ENDPOINT = "images/generations"
_POLL_INTERVAL_SECONDS = 5.0
_POLL_MAX_WAIT_SECONDS = 600.0


class KlingImageBackend(KlingBackendBase):
    """Asynchronous Kling image backend, JWT / Bearer authentication."""

    # Text-to-image and multi-image subject I2I share endpoints.
    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    # Channels impose no reference counts; model-specific subject maxima live in entries.
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = None

    _media_label = "图像"

    def __init__(
        self,
        *,
        auth_mode: str = "jwt",
        access_key: str | None = None,
        secret_key: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        api_model_name: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        super().__init__(
            auth_mode=auth_mode,
            access_key=access_key,
            secret_key=secret_key,
            api_key=api_key,
            model=model or DEFAULT_MODEL,
            base_url=base_url,
            http_timeout=http_timeout,
            transport_layer=transport_layer,
        )
        # API model names supplied after registry decoupling, defaulting to keys when ordinary keys equal API names.
        self._api_model_name = api_model_name or self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    # ── request building ────────────────────────────────────────────────

    def _build_payload(self, request: ImageGenerationRequest) -> dict:
        """Build image bodies: no references means T2I; references mean image-array I2I."""
        payload: dict = {
            "model_name": self._api_model_name,
            "prompt": request.prompt,
            "aspect_ratio": request.aspect_ratio,
            "n": 1,
        }
        images = self._encode_references(request.reference_images)
        if images:
            payload["image"] = images
        return payload

    def _encode_references(self, reference_images: list[ReferenceImage]) -> list[str]:
        """References → bare base64 lists without data-URI prefixes; missing/unreadable inputs fail loudly."""
        if not reference_images:
            return []
        encoded: list[str] = []
        unreadable: list[str] = []
        # names enter multilingual errors; empty paths use reference ordinal #N, never localized placeholders.
        for idx, ref in enumerate(reference_images, start=1):
            path = Path(ref.path) if ref.path else None
            if path is None or not path.is_file():
                # Empty path.name, such as . or /, falls back to #N to avoid blank error tokens.
                unreadable.append(path.name if (path and path.name) else f"#{idx}")
                continue
            try:
                encoded.append(image_to_base64(path))
            except OSError as exc:
                logger.warning("Kling 参考图读取失败: %s (%s)", path, exc)
                unreadable.append(path.name)
        if unreadable:
            # Fail loudly for missing declared images instead of billing incorrect subset-generated results.
            raise ImageCapabilityError(
                "image_reference_images_unreadable", model=self._model, names=", ".join(unreadable)
            )
        return encoded

    @staticmethod
    def _safe_log_view(payload: dict) -> dict:
        """Preredacted scalar views sent directly to logger, avoiding format_kwargs_for_log sinks.

        Never expand base64 references/prompts; report only presence, counts, and prompt lengths.
        """
        prompt = payload.get("prompt")
        images = payload.get("image")
        return {
            "endpoint": _IMAGE_ENDPOINT,
            "model_name": payload.get("model_name"),
            "aspect_ratio": payload.get("aspect_ratio"),
            "n": payload.get("n"),
            "reference_count": len(images) if isinstance(images, list) else int(bool(images)),
            "prompt_len": len(prompt) if isinstance(prompt, str) else 0,
        }

    # ── generate ────────────────────────────────────────────────────────

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        """Payloads omit resolution, so omit its usage dimension and bill the price table's default tier."""
        return per_image_usage(output_images=1, input_images=facts.reference_count)

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        payload = await asyncio.to_thread(self._build_payload, request)
        logger.info("调用 Kling 图像 API payload=%s", self._safe_log_view(payload))
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            task_id = await self._submit_task(client, _IMAGE_ENDPOINT, payload)
            logger.info("Kling 图像任务已创建: task_id=%s model=%s", task_id, self._model)

            final = await self._poll_until_terminal(
                lambda: self._poll_query(client, f"{_IMAGE_ENDPOINT}/{task_id}"),
                poll_interval=_POLL_INTERVAL_SECONDS,
                max_wait=_POLL_MAX_WAIT_SECONDS,
            )
            # Persist 24-hour image_url before expiry; take first output [0], with sets billed per produced image.
            download_url = extract_kling_image_urls(final)[0]

        await download_image_to_path(download_url, request.output_path)
        logger.info("Kling 图像下载完成: %s", request.output_path)

        images = payload.get("image")
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_KLING,
            model=self._model,
            image_uri=download_url,
            seed=request.seed,
            # Payloads omit resolution; omit its metering dimension too.
            usage=per_image_usage(
                output_images=1, input_images=len(images) if isinstance(images, list) else int(bool(images))
            ),
        )


class KlingSingleImageBackend(KlingImageBackend):
    """Single-reference Kling channel: ``image`` is a bare base64 string."""

    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = 1

    def _build_payload(self, request: ImageGenerationRequest) -> dict:
        limit = self.CHANNEL_REFERENCE_LIMIT
        if limit is not None and len(request.reference_images) > limit:
            raise ImageCapabilityError(
                "image_reference_images_exceeded",
                model=self._model,
                limit=limit,
                count=len(request.reference_images),
            )
        payload = super()._build_payload(request)
        if images := payload.get("image"):
            payload["image"] = images[0]
        return payload


KLING_IMAGE_BACKENDS: dict[str, type[KlingImageBackend]] = {
    "kling-image": KlingImageBackend,
    "kling-image-single": KlingSingleImageBackend,
}
