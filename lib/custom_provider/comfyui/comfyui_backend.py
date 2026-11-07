"""ComfyUI video channels.

Outside ``lib.backends.video_backends`` because inputs are ComfyUI definitions (workflow + bindings), requiring this package's
builders; ``pyproject.toml`` ``[tool.importlinter]`` forbids backend reverse dependencies on
``lib.custom_provider``. Like declarative runtimes, upper layers consume lower ones, which know no definitions.

Four generation stages: upload assets for server references → construct actual workflows on deep copies → ``POST /prompt``
for ``prompt_id`` → poll ``/history`` terminal states, extract ``output`` artifacts, download/persist. Upload before construction
because references enter workflows; persist ``provider_job_id`` before polling because process restarts
would otherwise permanently lose submitted tasks.

Stage four is media-independent, shared with images in :mod:`.comfyui_execution`; video-specific responsibilities:
binding-derived capabilities, first/last/reference uploads, resume, and converting retrieved artifacts into
``VideoGenerationResult``.

Resume enters stage four using ``provider_job_id`` = ``prompt_id``; prior processes already completed stages one-three. Cancellation/
timeout instead stops remote execution too, avoiding continued occupation of user GPUs after local abandonment.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import ProviderJobIdPersistenceMixin, notify_provider_response
from lib.custom_provider.comfyui.capabilities import derive_video_capabilities
from lib.custom_provider.comfyui.comfyui_client import ComfyuiClient, client_id_for, upload_filename
from lib.custom_provider.comfyui.comfyui_execution import HTTP_TIMEOUT_SECONDS, ComfyuiExecution, PickedArtifact
from lib.custom_provider.comfyui.request_builder import BuiltWorkflow, MediaInputs, build_workflow

logger = logging.getLogger(__name__)

#: Video outputs: artifact-extension allowlists/failure wording share this value
#: (``comfyui.artifacts.ARTIFACT_SUFFIXES_BY_MEDIA_TYPE``).
MEDIA_TYPE = "video"


class ComfyuiVideoBackend(ProviderJobIdPersistenceMixin):
    """Execute ComfyUI definitions into storyboard videos."""

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
        """``job_label`` identifies nonworker jobs in upload names/``client_id``.

        Workers omit it, using ``request.task_id`` as ArcReel identities. Without either,
        random hex cannot identify callers in ComfyUI queues but avoids
        collisions with others.
        """
        self._provider = provider_id
        self._model = model
        self._definition = definition
        self._job_label = job_label
        self._base_url = base_url
        self._api_key = api_key
        self._client = ComfyuiClient(base_url=base_url, api_key=api_key, definition=definition)
        self._execution = ComfyuiExecution(client=self._client, definition=definition, media_type=MEDIA_TYPE)

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def video_capabilities(self) -> VideoCapabilities:
        """Capabilities declared by this workflow's bindings.

        Not merely factory-bypass fallback: wrapper tier queries
        (``CustomVideoBackend.video_capabilities_for_tier``) deliberately bypass injected composed results,
        instead layering user overrides onto these wrapped declarations. Pregeneration gates follow that path;
        missing capability bits here reject requests before :meth:`generate`.
        """
        return binding_video_capabilities(self._definition)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        count = len(request.reference_images or [])
        slots = derive_video_capabilities(self._definition).max_reference_images
        if count > slots:
            raise VideoCapabilityError("video_reference_images_exceeded", model=self._model, limit=slots, count=count)
        job_label = request.task_id or self._job_label or uuid4().hex
        async with artifact_http_client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as http:
            media = await self._upload_media(http, request, job_label=job_label)
            built = build_workflow(
                self._definition,
                prompt=request.prompt,
                aspect_ratio=request.aspect_ratio,
                # Pass durations unchanged: unbound ``frames``/unreadable fps makes construction skip frame conversion.
                # Added defaults here would fabricate durations for workflows not driven by ArcReel.
                duration_seconds=request.duration_seconds,
                resolution=request.resolution,
                media=media,
                seed=request.seed,
            )
            prompt_id = await self._client.submit_prompt(
                http,
                built.workflow,
                client_id=client_id_for(job_label),
                record=lambda stage, body: notify_provider_response(request, stage, body),
            )
            await self._persist_provider_job_id(
                request, prompt_id, provider=self._provider, endpoint=self._client.base_url
            )
            picked = await self._execution.fetch_artifact(
                http,
                prompt_id,
                output_path=request.output_path,
                poll_timeout_seconds=request.poll_timeout_seconds,
                record=lambda stage, body: notify_provider_response(request, stage, body),
            )
            return self._result(request, prompt_id, picked, built=built)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Resume submitted execution by polling, without reuploading or resubmitting workflows.

        ``prompt_id`` is ``provider_job_id``; server-owned execution means resubmission would repeat the same image
        on user GPUs. ``output`` bindings use **current** definitions: changed bindings must govern retrieval,
        otherwise missing outputs raise ``comfyui_output_missing``.

        Actual seeds/workflow fingerprints are unavailable on resume: both exist only during submission construction, bypassed here.

        Hosts replay submission ``submitted_base_url`` via resume_executor task rows, matching declarative
        runtimes: providers may change base_url while tasks remain on original ComfyUI machines.
        Current hosts would query other machines, whose missing prompt_id would classify still-running tasks lost
        while original GPUs continue. Hosts are connection dimensions, not protocols.
        """
        submitted = request.submitted_base_url
        if submitted and submitted != self._base_url:
            return await self._bound_to(submitted).resume_video(job_id, request)
        async with artifact_http_client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as http:
            picked = await self._execution.fetch_artifact(
                http,
                job_id,
                output_path=request.output_path,
                poll_timeout_seconds=request.poll_timeout_seconds,
                record=lambda stage, body: notify_provider_response(request, stage, body),
                # Missing executions on resume mean resume_expired, letting workers mark failed/settle pending
                # calls rather than treating them as locally retryable generation failures.
                lost_error=lambda prompt_id: ResumeExpiredError(job_id=prompt_id, provider=self._provider),
            )
            return self._result(request, job_id, picked, built=None)

    def _bound_to(self, base_url: str) -> ComfyuiVideoBackend:
        """Same-definition instances bound to different hosts: polling/retrieval/stopping must all target one machine."""
        return ComfyuiVideoBackend(
            provider_id=self._provider,
            model=self._model,
            base_url=base_url,
            api_key=self._api_key,
            definition=self._definition,
            job_label=self._job_label,
        )

    def _result(
        self,
        request: VideoGenerationRequest,
        prompt_id: str,
        picked: PickedArtifact,
        *,
        built: BuiltWorkflow | None,
    ) -> VideoGenerationResult:
        """Convert retrieved artifacts into results; ``built`` = ``None`` means resume without construction."""
        warnings: tuple[Mapping[str, Any], ...] = ()
        if picked.count > 1:
            warnings = (
                {"key": "comfyui_multiple_outputs", "params": {"count": picked.count, "filename": picked.filename}},
            )
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self._provider,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=self._execution.view_url(picked.artifact),
            task_id=prompt_id,
            # Actual seeds/workflow fingerprints exist only at submission construction; together they identify source graphs/seeds
            # for versions. Resume constructs neither, so omit both instead of fabricating individual values.
            seed=built.seed if built is not None else None,
            generate_audio=request.generate_audio,
            provenance={"workflow_sha256": built.workflow_sha256} if built is not None else None,
            warnings=warnings,
        )

    # Uploads.

    async def _upload_media(
        self, http: httpx.AsyncClient, request: VideoGenerationRequest, *, job_label: str
    ) -> MediaInputs:
        """Upload bound first/last frames and every reference already validated for slot capacity."""
        bindings: Mapping[str, Any] = self._definition.get("bindings") or {}
        start = await self._upload_one(http, request.start_image, bindings, "start_image", job_label=job_label)
        end = await self._upload_one(http, request.end_image, bindings, "end_image", job_label=job_label)
        references: list[str] = []
        for index, path in enumerate(request.reference_images or []):
            references.append(
                await self._client.upload_image(
                    http, path, filename=upload_filename(job_label, "reference_images", path, index)
                )
            )
        return MediaInputs(start_image=start, end_image=end, reference_images=tuple(references))

    async def _upload_one(
        self,
        http: httpx.AsyncClient,
        path: Path | None,
        bindings: Mapping[str, Any],
        key: str,
        *,
        job_label: str,
    ) -> str | None:
        if path is None or not _targets(bindings.get(key)):
            return None
        return await self._client.upload_image(http, path, filename=upload_filename(job_label, key, path))


def _targets(raw: object) -> list[Mapping[str, Any]]:
    return [target for target in raw if isinstance(target, Mapping)] if isinstance(raw, list) else []


def binding_video_capabilities(definition: Mapping[str, Any]) -> VideoCapabilities:
    """Box video bindings into backend capability types.

    Derivation lives in ``comfyui``, knowing bindings/workflows only. Boxing lives here because forbidden dependencies
    on declarative runtimes prevent that package reaching ``VideoCapabilities``. Projections
    (``endpoints.comfyui_endpoint_spec``) and backend declarations share one implementation;
    they feed different gate stages but must convey identical capabilities.

    Three audio-reference dimensions and ``max_prompt_chars`` retain defaults: bindings lack corresponding keys.
    """
    bound = derive_video_capabilities(definition)
    return VideoCapabilities(
        text_to_video=bound.text_to_video,
        first_frame=bound.first_frame,
        last_frame=bound.last_frame,
        max_reference_images=bound.max_reference_images,
        audio_track=VideoAudioMode(bound.audio_track),
    )
