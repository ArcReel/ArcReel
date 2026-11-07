"""ComfyUI image channels.

Share :mod:`.comfyui_client`, :mod:`.request_builder`, and :mod:`.comfyui_execution` with videos
(:mod:`.comfyui_backend`). Only image-specific responsibilities remain: reference uploads, ``bindings``-derived
capabilities/reference limits, and converting retrieved artifacts into ``ImageGenerationResult``.

Two substantive video differences:

* **No resume.** ``ImageBackend`` lacks ``resume``, leaving nowhere to persist ``prompt_id``
  (``ImageGenerationRequest`` lacks ``task_id``). Midpoll restarts lead worker orphan handling to
  ``restart_lost_image``; submission already happened, so resubmitting repeats images on user GPUs.
  Cancellation/timeouts still stop remotes because current ``prompt_id`` needs no persistence.
* **Multiple artifacts log only.** ``batch_size > 1`` produces several images, selecting first;
  ``ImageGenerationResult`` lacks video-style ``result.warnings``, so warnings enter logs only.
  Import inference separately exposes ``batch_size_above_one`` before endpoint saves.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from arcreel_market_core.comfyui.bindings import targets_of
from arcreel_market_core.video_backend_contract import ProviderResponseStage
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import notify_provider_response_to
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
)
from lib.custom_provider.comfyui.capabilities import takes_reference_images
from lib.custom_provider.comfyui.comfyui_client import ComfyuiClient, client_id_for, upload_filename
from lib.custom_provider.comfyui.comfyui_execution import HTTP_TIMEOUT_SECONDS, ComfyuiExecution
from lib.custom_provider.comfyui.request_builder import MediaInputs, build_workflow

logger = logging.getLogger(__name__)

#: Image outputs: artifact-extension allowlists/failure wording share this value
#: (``comfyui.artifacts.ARTIFACT_SUFFIXES_BY_MEDIA_TYPE``).
MEDIA_TYPE = "image"

#: Image execution wall-clock maximum.
#:
#: Never read global ``video_poll_timeout_seconds``: silently extending video settings to images obscures affected behaviors
#: when users adjust them. Images have no configurable timeout; built-ins make one synchronous call,
#: but ComfyUI queues/runs on user GPUs, requiring generous fixed worst-case limits.
IMAGE_POLL_TIMEOUT_SECONDS = 1800


class ComfyuiImageBackend:
    """Execute ComfyUI definitions into images.

    Implement ``lib.backends.image_backends.base.ImageBackend``. Four stages match videos: upload references
    for server names → construct on deep copies → ``POST /prompt`` for ``prompt_id`` →
    poll ``/history`` terminal states, retrieve/persist ``output``-bound artifacts.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        model: str,
        base_url: str,
        api_key: str,
        definition: Mapping[str, Any],
        job_label: str | None = None,
    ) -> None:
        """``job_label`` enters upload names/``client_id`` to identify callers in ComfyUI queues.

        Images lack ``task_id`` (``ImageGenerationRequest`` has no such dimension), without worker/nonworker
        distinctions. Use supplied labels, otherwise random hex: unidentified callers,
        but no collisions/overwritten uploads.
        """
        self._provider = provider_id
        self._model = model
        self._definition = definition
        self._job_label = job_label
        self._client = ComfyuiClient(base_url=base_url, api_key=api_key, definition=definition)
        self._execution = ComfyuiExecution(client=self._client, definition=definition, media_type=MEDIA_TYPE)

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        """Workflow capabilities from bindings.

        Projection ``endpoints.comfyui_endpoint_spec`` shares :func:`binding_image_capabilities`:
        projections choose buckets; this declaration gates ``media_generator`` requests. Both must agree,
        avoiding I2I-bucket endpoints rejecting themselves at execution.
        """
        return set(binding_image_capabilities(self._definition))

    async def reference_image_limit(self) -> int | None:
        return binding_reference_limit(self._definition)

    @property
    def _reference_slots(self) -> int:
        return len(targets_of((self._definition.get("bindings") or {}).get("reference_images")))

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        count = len(request.reference_images)
        if count > self._reference_slots:
            raise ImageCapabilityError(
                "image_reference_images_exceeded", model=self._model, limit=self._reference_slots, count=count
            )
        job_label = self._job_label or uuid4().hex
        async with artifact_http_client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as http:
            media = await self._upload_media(http, request, job_label=job_label)
            built = build_workflow(
                self._definition,
                prompt=request.prompt,
                aspect_ratio=request.aspect_ratio,
                resolution=request.image_size,
                media=media,
                seed=request.seed,
            )

            async def record(stage: ProviderResponseStage, body: object) -> None:
                await notify_provider_response_to(request.on_provider_response, stage, body)

            prompt_id = await self._client.submit_prompt(
                http,
                built.workflow,
                client_id=client_id_for(job_label),
                record=record,
            )
            picked = await self._execution.fetch_artifact(
                http,
                prompt_id,
                output_path=request.output_path,
                poll_timeout_seconds=IMAGE_POLL_TIMEOUT_SECONDS,
                record=record,
            )
            return ImageGenerationResult(
                image_path=request.output_path,
                provider=self._provider,
                model=self._model,
                image_uri=self._execution.view_url(picked.artifact),
                # Actual seeds: ``policy: "random"`` resolves at submission, not from request values. Report them
                # rather than omit: image calls do not merge version metadata (``_merge_result_provenance`` belongs
                # only to videos), making this field the sole place identifying version seeds here.
                seed=built.seed,
            )

    async def _upload_media(
        self, http: httpx.AsyncClient, request: ImageGenerationRequest, *, job_label: str
    ) -> MediaInputs:
        """Upload every reference already validated for slot capacity."""
        references: list[str] = []
        for index, reference in enumerate(request.reference_images):
            path = Path(reference.path)
            references.append(
                await self._client.upload_image(
                    http, path, filename=upload_filename(job_label, "reference_images", path, index)
                )
            )
        return MediaInputs(reference_images=tuple(references))


def binding_reference_limit(definition: Mapping[str, Any]) -> int | None:
    """Workflow reference maxima equal reference-slot counts; ``None`` without slots.

    Extra images have no construction targets, so orchestration trims/warns upfront rather than silently discard user choices.
    Without slots, workflows lack I2I (:func:`binding_image_capabilities`); effective limits mean unsupported regardless of counts.
    """
    slots = len(targets_of((definition.get("bindings") or {}).get("reference_images")))
    return slots or None


def binding_image_capabilities(definition: Mapping[str, Any]) -> frozenset[ImageCapability]:
    """Box image bindings into backend capability types.

    Reference slots mean I2I only, otherwise T2I only, mutually exclusive (``docs/adr/0082``). Predicates live in ``comfyui``,
    knowing bindings/workflows only; boxing lives here because forbidden runtime dependencies prevent that
    package reaching ``ImageCapability``. Mirrors video ``binding_video_capabilities`` placement.
    """
    if takes_reference_images(definition):
        return frozenset({ImageCapability.IMAGE_TO_IMAGE})
    return frozenset({ImageCapability.TEXT_TO_IMAGE})
