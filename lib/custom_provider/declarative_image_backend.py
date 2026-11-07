"""Declarative image channels: image requests/artifact semantics over :class:`DeclarativeJobEngine`.

Share submission/polling/status mapping/secondary retrieval/downloads with videos; images add template variables,
capabilities, and output fields for URLs or response-inline base64.

Images lack resume protocols: restart recovery marks in-flight image tasks restart-lost, so never persist provider task ids here;
failed retrieval cannot reconnect original tasks, requiring regeneration.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from arcreel_market_core.video_backend_contract import ProviderJobStatus
from lib.backends.artifact_download_guard import IMAGE_ARTIFACT_MAX_BYTES, artifact_http_client
from lib.backends.backend_runtime import ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS
from lib.backends.container_sniff import CONTAINER_HEAD_BYTES, sniff_container
from lib.backends.image_backends.base import ImageCapability, ImageGenerationRequest, ImageGenerationResult
from lib.custom_provider.declarative_backend import (
    DeclarativeJobEngine,
    DeclarativeRuntimeError,
    JobCall,
    JobState,
    extract_job_status,
    extract_text,
    response_extract_guard,
)
from lib.custom_provider.path_overrides import NO_PATH_OVERRIDES, PathOverrides
from lib.infra.async_thread import run_noninterruptible_sync

_HTTP_TIMEOUT_SECONDS = 60

#: Image-task terminal wall-clock maximum.
#:
#: Never read global ``video_poll_timeout_seconds``: extending video settings to images would obscure how many behaviors users change.
#: Images have no configurable timeout; use the same fixed value as ComfyUI image endpoints.
IMAGE_POLL_TIMEOUT_SECONDS = 1800

#: Failures after provider generation but unsuccessful retrieval/persistence; recover through regeneration.
IMAGE_SAVE_FAILED_CODE = "declarative_image_save_failed"

#: Image-definition ``capabilities`` fields → endpoint capabilities.
_IMAGE_CAPABILITY_BY_FIELD: Mapping[str, ImageCapability] = {
    "text_to_image": ImageCapability.TEXT_TO_IMAGE,
    "image_to_image": ImageCapability.IMAGE_TO_IMAGE,
}


def definition_reference_limit(definition: Mapping[str, Any]) -> int | None:
    """Declared reference maxima (channel facts); ``None`` without positive declarations means no channel limit.

    ``max_reference_images: 0`` preserves protocol meaning, undeclared maxima. Validators require positive limits when image-to-image is declared.
    """
    value = (definition.get("capabilities") or {}).get("max_reference_images")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def image_capabilities_from_definition(definition: Mapping[str, Any]) -> frozenset[ImageCapability]:
    """Explicit image capabilities shared by endpoint projections/backends, preventing differing declarations."""
    declared: Mapping[str, Any] = definition.get("capabilities") or {}
    return frozenset(
        capability for name, capability in _IMAGE_CAPABILITY_BY_FIELD.items() if declared.get(name) is True
    )


@dataclass(frozen=True)
class ImageJobState(JobState):
    """Image interpretation results: :class:`JobState` plus artifact URLs/inline base64."""

    image_url: str | None
    #: Raw undecoded ``image_b64``; prefer ``image_url`` if both present.
    image_b64: str | None


_DATA_URI_PREFIX = re.compile(r"^data:image/[\w.+-]+;base64,", re.IGNORECASE)

#: Recognized containers before inline persistence; unknown bytes are not saved as images.
_INLINE_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})


def decode_image_b64(text: str) -> bytes:
    """Decode raw ``image_b64`` into image bytes, accepting bare base64 and ``data:image/...;base64,``.

    Raises:
        ValueError: Invalid/empty base64, oversized artifacts, or headers other than PNG / JPEG / WebP.
    """
    payload = "".join(_DATA_URI_PREFIX.sub("", text.strip(), count=1).split())
    # Estimate sizes from encoded lengths before decoding, avoiding memory costs for oversized payloads.
    if len(payload) // 4 * 3 > IMAGE_ARTIFACT_MAX_BYTES:
        raise ValueError(f"inline image exceeds {IMAGE_ARTIFACT_MAX_BYTES} bytes")
    try:
        image = base64.b64decode(payload + "=" * (-len(payload) % 4), validate=True)
    except binascii.Error as exc:
        raise ValueError("image_b64 is not valid base64") from exc
    if not image:
        raise ValueError("image_b64 decoded to an empty image")
    # Alphanumeric strings such as ``SUCCESS`` may be valid base64; decodable does not mean images.
    if sniff_container(image[:CONTAINER_HEAD_BYTES]) not in _INLINE_IMAGE_MIMES:
        raise ValueError("image_b64 decoded to bytes that are not a PNG, JPEG or WebP image")
    return image


def extract_image_state(
    body: object,
    extract: Mapping[str, Any],
    *,
    status_map: Mapping[str, str] | None = None,
    status: ProviderJobStatus | None = None,
) -> ImageJobState:
    """Read response bodies through one image-definition ``extract`` section; sole interpretation shared by runtime/response validation."""
    with response_extract_guard():
        job_status, provider_status = extract_job_status(body, extract, status_map=status_map, status=status)
        return ImageJobState(
            body=body,
            status=job_status,
            provider_status=provider_status,
            image_url=extract_text(extract.get("image_url"), body),
            image_b64=extract_text(extract.get("image_b64"), body),
            error=extract_text(extract.get("error"), body),
            result_id=extract_text(extract.get("result_id"), body),
        )


class DeclarativeImageBackend:
    """Declarative image channels implementing ``lib.backends.image_backends.base.ImageBackend``."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        definition: Mapping[str, Any],
        provider: str,
        path_overrides: PathOverrides = NO_PATH_OVERRIDES,
    ) -> None:
        self._engine = DeclarativeJobEngine(
            api_key=api_key,
            base_url=base_url,
            model=model,
            definition=definition,
            provider=provider,
            read_state=extract_image_state,
            log_label="声明式图片请求",
            path_overrides=path_overrides,
        )
        self._model = model
        self._definition = definition
        self._provider = provider

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(image_capabilities_from_definition(self._definition))

    async def reference_image_limit(self) -> int | None:
        return self._reference_limit

    @property
    def _reference_limit(self) -> int | None:
        return definition_reference_limit(self._definition)

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        references = request.reference_images
        context = await asyncio.to_thread(
            self._engine.request_context,
            {
                "prompt": request.prompt,
                "aspect_ratio": request.aspect_ratio,
                "resolution": request.image_size,
                "seed": request.seed,
            },
            {"reference_images": [Path(ref.path) for ref in references]},
            require_declared_inputs=True,
        )
        call = JobCall(
            poll_timeout_seconds=IMAGE_POLL_TIMEOUT_SECONDS,
            on_provider_response=request.on_provider_response,
            label=None,
        )
        async with artifact_http_client(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            job_id = await self._engine.submit(client, context, call)
            try:
                return await self._collect(client, job_id, call, context, request)
            except DeclarativeRuntimeError as exc:
                # Engine download/secondary-retrieval exhaustion uses retry-download codes requiring resumed original tasks;
                # images persist no ids and cannot resume, so require regeneration.
                if exc.code != "artifact_download_failed":
                    raise
                raise DeclarativeRuntimeError(IMAGE_SAVE_FAILED_CODE, detail=str(exc)) from exc

    async def _collect(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        call: JobCall,
        context: Mapping[str, object],
        request: ImageGenerationRequest,
    ) -> ImageGenerationResult:
        outcome = await self._engine.poll(client, job_id, call, context=context, is_resume=False)
        final = outcome.result_state or outcome.poll_state
        if not final.image_url:
            await _write_inline_image(final, request.output_path)
            return self._result(request, image_uri=None)
        await self._engine.download(
            client,
            final.image_url,
            request.output_path,
            context,
            max_bytes=IMAGE_ARTIFACT_MAX_BYTES,
            max_wait=ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS,
            trusted_origins=outcome.trusted_origins,
        )
        return self._result(request, image_uri=final.image_url)

    def _result(self, request: ImageGenerationRequest, *, image_uri: str | None) -> ImageGenerationResult:
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self._provider,
            model=self._model,
            image_uri=image_uri,
            seed=request.seed,
        )


async def _write_inline_image(state: ImageJobState, output_path: Path) -> None:
    """Persist base64 when URLs unavailable; neither present or undecodable images mean retrieval failures."""
    if not state.image_b64:
        raise DeclarativeRuntimeError(
            "declarative_response_extract_failed",
            detail=state.error or "provider reported success but no image matched the definition",
        )
    encoded = state.image_b64

    def decode_and_save() -> None:
        image = decode_image_b64(encoded)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image)

    try:
        # Large-image decoding is CPU-heavy; offload to threads to preserve event loops.
        await run_noninterruptible_sync(decode_and_save)
    except ValueError as exc:
        raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=str(exc)) from exc
    except OSError as exc:
        raise DeclarativeRuntimeError(IMAGE_SAVE_FAILED_CODE, detail=str(exc)) from exc
