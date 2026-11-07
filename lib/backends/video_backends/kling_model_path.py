"""KlingModelPathVideoBackend: new Kling API video channel (endpoint ``kling-video-model-path``).

New APIs put model names in paths and accept API Key (Bearer) only; see ``docs/api-docs/endpoints/kling-video-model-path.md``:

- Text-to-video ``POST /text-to-video/<模型名>``: top-level ``prompt`` plus ``settings.{resolution,duration,aspect_ratio}``.
- Image-to-video ``POST /image-to-video/<模型名>``: ``contents[]`` contains prompts (``prompt``), first frames (``first_frame``), optional
  last frames (``last_frame``); ``settings`` contains only ``resolution`` and ``duration``, with aspect ratio following first frames.
- Read ``data.id`` on submission; poll ``GET /tasks?task_ids=<id>`` until ``status=succeeded``, retrieve ``outputs[type=video].url``,
  and bill duration from ``usage.output_duration``.

No audio switch exists in bodies; output always has audio (officially audiovisual synchronization/intelligent storyboarding have no switch and default on, including APIs).
This channel accepts no reference images/videos or subjects (omni reference ``/omni-video``): official input-video billing takes the greater of
output×2 and input+output, unrepresentable by current metering. Model entries record accepted inputs/durations/resolutions, evaluated jointly with channel declarations (docs/adr/0104).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import cast

import httpx

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.video_backend_contract import (
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import (
    ProviderJobIdPersistenceMixin,
    download_resumable_video,
    recording_poll,
    resume_expiry_gate,
)
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.backends.kling_backend_base import KlingBackendBase
from lib.backends.kling_shared import (
    extract_kling_model_path_video_url,
    image_to_base64,
    is_kling_model_path_terminal,
    kling_model_path_base_url,
    kling_model_path_failure_reason,
    kling_model_path_output_duration,
    kling_model_path_status,
    kling_model_path_task_id,
)
from lib.backends.providers import PROVIDER_KLING
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import EntryBoundCapabilities
from lib.billing.metering import Usage

logger = logging.getLogger(__name__)

ENDPOINT_KEY = "kling-video-model-path"
DEFAULT_MODEL = "kling-4.0"

TEXT_TO_VIDEO = "text-to-video"
IMAGE_TO_VIDEO = "image-to-video"
TASKS_PATH = "tasks"

# Persisted job_id prefix: new API has one id-based /tasks query, without encoding subpaths as old APIs did.
# Prefix recognizes old-channel ids such as ``text2video:<id>:<audio>`` to avoid sending them to new query endpoints.
_JOB_ID_PREFIX = "tasks:"

# Send 720p when unspecified, accepted by kling-4.0, kling-4.0-flash, and kling-3.0-turbo;
# avoid upstream defaults (kling-4.0 defaults to 1080p) mismatching metered resolution tiers.
_DEFAULT_RESOLUTION = "720p"

# Channel can send text, first frames, and last frames for all models; no reference-image/audio request fields.
_CHANNEL_CAPABILITIES = VideoCapabilities(
    text_to_video=True,
    first_frame=True,
    last_frame=True,
    max_reference_images=0,
    audio_track=VideoAudioMode.ALWAYS_ON,
)


def _has_image(value: object) -> bool:
    return isinstance(value, (str, Path)) and bool(str(value))


def _route_for(request: VideoGenerationRequest) -> str:
    return IMAGE_TO_VIDEO if _has_image(request.start_image) else TEXT_TO_VIDEO


def _resolution_for(request: VideoGenerationRequest) -> str:
    return (request.resolution or _DEFAULT_RESOLUTION).lower()


def encode_job_id(task_id: str) -> str:
    return f"{_JOB_ID_PREFIX}{task_id}"


def decode_job_id(job_id: str) -> str:
    """Restore new-API task ids from persisted job_id; reject ids not written by this channel instead of querying them."""
    task_id = job_id.removeprefix(_JOB_ID_PREFIX)
    if task_id == job_id or not task_id:
        raise ValueError(f"可灵新版 API 认不出这个任务 ID，它不是由本调用端点提交的: {job_id}")
    return task_id


class KlingModelPathVideoBackend(EntryBoundCapabilities, KlingBackendBase, ProviderJobIdPersistenceMixin):
    """New Kling API video backend (Bearer API Key, asynchronous polling)."""

    _media_label = "视频"
    endpoint_key = ENDPOINT_KEY

    def __init__(
        self,
        *,
        auth_mode: str = "bearer",
        access_key: str | None = None,
        secret_key: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        if auth_mode != "bearer":
            # Official authentication: Access Key / Secret Key apply only to old-standard APIs; new APIs accept API Key only.
            raise ValueError("可灵新版 API 只支持 API Key 鉴权，请到系统配置页填写可灵 Kling 的 API Key")
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
        self._base_url = kling_model_path_base_url(base_url)

    # Capabilities and metering.

    @classmethod
    def video_capabilities_for_model(cls, model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """Channel facts: text, first frames, and last frames can be sent; references/audio references cannot; output always has audio.

        Only kling-4.0 accepts last frames (kling-4.0-flash/kling-3.0-turbo accept first frames only), a model fact narrowed by entries.
        """
        return _CHANNEL_CAPABILITIES

    @classmethod
    def effective_generate_audio_for_model(cls, model: str, entry: CatalogEntry | None = None) -> bool:
        return True

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model, self._catalog_entry)

    @classmethod
    def metered_usage(
        cls,
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """This request's measurements (:mod:`lib.backends.video_backends.metering`).

        Resolution is actual sent ``settings.resolution`` (720p if unspecified); output always has audio. Settled output seconds use
        ``usage.output_duration`` (official billing duration, reported only by kling-4.0 families), otherwise sent duration.
        """
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=_resolution_for(request),
            audio=True,
            reference_images=0,
        )

    # Response interpretation.

    def _task_id_from(self, submit_payload: dict) -> str:
        return kling_model_path_task_id(submit_payload)

    def _task_status(self, payload: dict) -> str | None:
        return kling_model_path_status(payload)

    def _is_terminal(self, payload: dict) -> bool:
        return is_kling_model_path_terminal(payload)

    def _failure_reason(self, payload: dict) -> str | None:
        return kling_model_path_failure_reason(payload)

    # Request construction.

    def _build_payload(self, request: VideoGenerationRequest) -> tuple[str, dict]:
        """Return (submission path, body); reject unsendable inputs before billing, never silently discard."""
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)
        if request.reference_images and any(_has_image(p) for p in request.reference_images):
            raise VideoCapabilityError("video_reference_images_unsupported", model=self._model)
        # No body audio switch; silent requests reach here only when users relax capability overrides.
        if not request.generate_audio:
            raise VideoCapabilityError("video_audio_switch_not_supported", provider=self.name, model=self._model)

        caps = request.video_capabilities or self.video_capabilities
        settings: dict = {"resolution": _resolution_for(request), "duration": int(request.duration_seconds)}
        route = _route_for(request)
        model_path = f"{route}/{self._model}"

        if route == TEXT_TO_VIDEO:
            if not caps.text_to_video:
                raise VideoCapabilityError("video_capability_missing_t2v", provider=self.name, model=self._model)
            if _has_image(request.end_image):
                raise VideoCapabilityError("video_end_image_requires_start_image", model=self._model)
            settings["aspect_ratio"] = request.aspect_ratio
            return model_path, {"prompt": request.prompt, "settings": settings}

        contents: list[dict] = []
        if request.prompt:
            contents.append({"type": "prompt", "text": request.prompt})
        contents.append({"type": "first_frame", "url": self._encode_frame(Path(cast(Path, request.start_image)))})
        if _has_image(request.end_image):
            if not caps.last_frame:
                raise VideoCapabilityError("video_last_frame_unsupported", provider=self.name, model=self._model)
            contents.append({"type": "last_frame", "url": self._encode_frame(Path(cast(Path, request.end_image)))})
        # Image-to-video has no aspect_ratio field; ratio follows first frames.
        return model_path, {"contents": contents, "settings": settings}

    def _encode_frame(self, path: Path) -> str:
        # Official frame fields accept URL/base64; declared unreadable frames abort instead of silently degrading to text-to-video.
        if not path.is_file():
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name)
        try:
            return image_to_base64(path)
        except OSError as exc:
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name) from exc

    @staticmethod
    def _safe_log_view(path: str, payload: dict) -> dict:
        """Preredacted scalar views: never expand frame base64 or prompts."""
        raw_settings = payload.get("settings")
        settings: dict = raw_settings if isinstance(raw_settings, dict) else {}
        raw_contents = payload.get("contents")
        contents: list = raw_contents if isinstance(raw_contents, list) else []
        frame_types = [c.get("type") for c in contents if isinstance(c, dict) and c.get("type") != "prompt"]
        prompt = payload.get("prompt")
        if not isinstance(prompt, str):
            prompt = next((c.get("text") for c in contents if isinstance(c, dict) and c.get("type") == "prompt"), "")
        return {
            "endpoint": path,
            "resolution": settings.get("resolution"),
            "duration": settings.get("duration"),
            "aspect_ratio": settings.get("aspect_ratio", ""),
            "frames": ",".join(str(t) for t in frame_types),
            "prompt_len": len(prompt) if isinstance(prompt, str) else 0,
        }

    # ── generate / resume ───────────────────────────────────────────────

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        path, payload = await asyncio.to_thread(self._build_payload, request)
        logger.info("调用 Kling 新版视频 API payload=%s", self._safe_log_view(path, payload))
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            task_id = await self._submit_task(client, path, payload, request)
            logger.info("Kling 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            # Persist actual request host too: resume replays it, otherwise changed base_url would query old tasks on new hosts.
            await self._persist_provider_job_id(
                request, encode_job_id(task_id), provider=PROVIDER_KLING, endpoint=self._base_url
            )
            return await self._poll_and_build(client, task_id, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Resume submitted tasks with polling/retrieval only, never resubmit (ADR 0007)."""
        task_id = decode_job_id(job_id)
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            return await self._poll_and_build(client, task_id, request, resume_job_id=job_id)

    async def _query_task(self, client: httpx.AsyncClient, task_id: str, *, base_url: str) -> dict:
        resp = await client.get(f"{base_url}/{TASKS_PATH}", params={"task_ids": task_id}, headers=self._headers())
        raise_for_status_redacted(resp)
        return resp.json()

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        resume_job_id: str | None = None,
    ) -> VideoGenerationResult:
        base_url = (request.submitted_base_url or self._base_url).rstrip("/")
        gated_poll = resume_expiry_gate(
            recording_poll(lambda: self._query_task(client, task_id, base_url=base_url), request),
            resume_job_id=resume_job_id,
            provider=PROVIDER_KLING,
        )
        final = await self._poll_until_terminal(gated_poll, max_wait=request.poll_timeout_seconds)

        download_url = extract_kling_model_path_video_url(final)
        await download_resumable_video(download_url, request.output_path, label="Kling")
        logger.info("Kling 视频下载完成: %s", request.output_path)

        billed_seconds = kling_model_path_output_duration(final)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_KLING,
            model=self._model,
            duration_seconds=billed_seconds if billed_seconds is not None else request.duration_seconds,
            video_uri=download_url,
            task_id=task_id,
            generate_audio=True,
        )
