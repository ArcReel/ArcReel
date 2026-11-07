"""GrokVideoBackend: xAI Grok video-generation backend."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.video_backend_contract import (
    IMAGE_MIME_TYPES,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.backend_runtime import download_video
from lib.backends.data_uri import image_to_data_uri
from lib.backends.grok_shared import create_grok_client
from lib.backends.providers import PROVIDER_GROK
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import EntryBoundCapabilities
from lib.billing.metering import Usage
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.infra.logging_utils import format_kwargs_for_log

logger = logging.getLogger(__name__)


# Every reference-to-video model (reference images or last frames) caps resolution: “Reference-to-video is capped at 720p”; first-frame +
# last_frame counts as reference-to-video (https://docs.x.ai/developers/model-capabilities/video/generation).
# Only SDK resolution enum 1080p exceeds that cap.
_REFERENCE_ROUTE_MAX_RESOLUTION = "720p"


class GrokVideoBackend(EntryBoundCapabilities):
    """xAI Grok video-generation backend."""

    endpoint_key = "grok-video-classic"
    mixed_frames_and_references = False

    DEFAULT_MODEL = "grok-imagine-video"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_grok_client(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL

    @property
    def name(self) -> str:
        return PROVIDER_GROK

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """xAI video-channel declarations (channel facts): pure computation without SDK clients or api_key.

        All models use the same construction to send first frames (``image_url``), last frames (``last_frame_url``), and reference images
        (``reference_image_urls``), without count limits. Reference audio accepts only preset ``voice_id``, unable to express project audio files, so remains undeclared.
        Entries record model-specific accepted inputs (1.5 lite rejects references, only 1.5 accepts last frames) and reference counts, jointly
        evaluated with declarations (``lib.backends.video_capabilities``, docs/adr/0104).

        Input-combination limits remain in ``generate`` request validation (shared by both endpoints): reference-to-video caps resolution at 720p;
        classic rejects simultaneous first frames and references.

        Audio toggles: requests send audio intent as SDK ``generate_audio`` (default audio, ``False`` produces silent
        video); ``generate`` settles using that same sent value.
        """
        del model, entry
        return VideoCapabilities(last_frame=True, max_reference_images=None, audio_track=VideoAudioMode.CONTROLLABLE)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model, self._catalog_entry)

    @staticmethod
    def metered_usage(
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """This request's measurements (:mod:`lib.backends.video_backends.metering`); settlement uses xAI-reported output duration.

        All models meter the same request fields, without model/entry distinctions.
        """
        del model, entry
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=request.resolution,
            audio=request.generate_audio,
        )

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        # Grok APIs are synchronous, without resumable job_id; orphan handlers mark [resume_unsupported] from NotImplementedError.
        raise NotImplementedError("GrokVideoBackend 不支持 resume_video（同步型 API）")

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Generate videos without black-box generation retries; only downloads after URL receipt can retry independently."""
        # No reference-audio body field; even user-relaxed capabilities cannot send it: reject before billing, never silently discard.
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)
        self._check_reference_route_resolution(request)
        self._check_classic_first_frame_with_references(request)
        # The SDK combines submit and provider-side waiting in one opaque call. Once it starts, an exception
        # cannot prove the provider rejected the request before accepting a paid job, so close MediaGenerator's
        # reference-payload compression retry window before entering it.
        if request.on_provider_resubmit_unsafe is not None:
            request.on_provider_resubmit_unsafe()
        response = await self._create_video(request)

        video_url = response.url
        # SDK fields lack types; narrow response duration to int for ledger Integer columns,
        # accepting float strings such as "15.0" through float first. Missing/unparseable (including inf/nan)/nonpositive/
        # unreasonably high durations fall back to request duration, keeping results positive and persistable.
        raw_duration = getattr(response, "duration", None)
        actual_duration = request.duration_seconds
        try:
            if raw_duration is not None:
                parsed = float(raw_duration)
                # Check raw values before rounding: 86400.9 exceeds 24h and cannot become acceptable by rounding down.
                if 0 < parsed <= MAX_BILLED_DURATION_SECONDS:
                    # Half-up rounding matches dashscope extract_billing_duration, avoiding truncated underbilling;
                    # values in (0, 0.5) rounding to zero also fall back, keeping results positive.
                    rounded = int(parsed + 0.5)
                    if rounded > 0:
                        actual_duration = rounded
        except (TypeError, ValueError, OverflowError):
            # Parsing failure is expected fallback for untyped SDK fields; keep request duration without raising.
            logger.debug("Grok 回报的 duration 无法解析: %r，回落请求时长 %s 秒", raw_duration, actual_duration)

        await download_video(video_url, request.output_path, label="Grok")
        logger.info("Grok 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            duration_seconds=actual_duration,
            video_uri=video_url,
            generate_audio=request.generate_audio,
        )

    def _check_reference_route_resolution(self, request: VideoGenerationRequest) -> None:
        """Reject reference-to-video resolutions above 720p before billed calls, avoiding provider errors or silent downgrades."""
        resolution = (request.resolution or "").strip().lower()
        if resolution != "1080p":
            return
        if request.reference_images or request.end_image:
            raise VideoCapabilityError(
                "video_reference_resolution_unsupported",
                model=self._model,
                resolution=resolution,
                max_resolution=_REFERENCE_ROUTE_MAX_RESOLUTION,
            )

    def _check_classic_first_frame_with_references(self, request: VideoGenerationRequest) -> None:
        """Classic models reject simultaneous first frames/references (reference-to-video docs: “Classic grok-imagine-video ... rejects combining
        ``image`` with reference inputs”); reject before billing rather than sending both for provider failure.

        Selected endpoints declare whether mixing is allowed, independently of model names/entries."""
        if not self.mixed_frames_and_references and request.start_image and request.reference_images:
            raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._model)

    async def _create_video(self, request: VideoGenerationRequest):
        """Generate videos through SDK calls with indeterminate submission-acceptance boundaries."""
        generate_kwargs = {
            "prompt": request.prompt,
            "model": self._model,
            "duration": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
            "generate_audio": request.generate_audio,
            # SDK polls internally; still enforce snapshot global timeout or this setting would uniquely fail to affect Grok.
            "timeout": timedelta(seconds=request.poll_timeout_seconds),
            "interval": timedelta(seconds=5),
        }
        if request.resolution is not None:
            generate_kwargs["resolution"] = request.resolution

        # Existence checks and image reads/data URI conversion are blocking I/O: offload together to threads.
        generate_kwargs.update(await asyncio.to_thread(self._encode_image_inputs, request))

        logger.info("Grok 视频生成开始: model=%s, duration=%ds", self._model, request.duration_seconds)
        logger.info("调用 %s 视频 SDK kwargs=%s", self.name, format_kwargs_for_log(generate_kwargs))
        return await self._client.video.generate(**generate_kwargs)

    def _encode_image_inputs(self, request: VideoGenerationRequest) -> dict[str, Any]:
        """First / last / reference images → SDK data URI parameters (sync local reads; callers run in threads)."""
        kwargs: dict[str, Any] = {}
        if request.start_image:
            image_path = Path(request.start_image)
            # Missing first frames cannot be silently skipped: output would still bill without using them.
            if not image_path.is_file():
                raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=image_path.name)
            kwargs["image_url"] = image_to_data_uri(image_path, IMAGE_MIME_TYPES)

        if request.end_image:
            # gate_video_request checks effective last_frame capabilities upfront (only 1.5 model entries declare last frames).
            end_path = Path(request.end_image)
            if not end_path.is_file():
                # Missing last frames cannot be silently skipped: output would still bill without reaching storyboard-required endings.
                raise VideoCapabilityError(
                    "video_end_image_unreadable", model=self._model, name=end_path.name or str(end_path)
                )
            kwargs["last_frame_url"] = image_to_data_uri(end_path, IMAGE_MIME_TYPES)

        if request.reference_images:
            missing = [p.name for p in request.reference_images if not p.is_file()]
            if missing:
                raise VideoCapabilityError(
                    "video_reference_images_unreadable", model=self._model, names=", ".join(missing)
                )
            kwargs["reference_image_urls"] = [image_to_data_uri(p, IMAGE_MIME_TYPES) for p in request.reference_images]
        return kwargs


class GrokClassicVideoBackend(GrokVideoBackend):
    """Classic protocol rejects mixed first-frame and reference images."""

    endpoint_key = "grok-video-classic"
    mixed_frames_and_references = False


class GrokMixedVideoBackend(GrokVideoBackend):
    """Request contract permitting mixed first-frame and reference images."""

    endpoint_key = "grok-video-mixed"
    mixed_frames_and_references = True


GROK_BACKENDS = {backend.endpoint_key: backend for backend in (GrokClassicVideoBackend, GrokMixedVideoBackend)}
