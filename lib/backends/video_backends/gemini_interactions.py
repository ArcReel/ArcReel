"""Gemini Interactions API video channel (Gemini Omni).

A protocol separate from Veo ``generate_videos`` long-running operations: submit ``interactions.create(background=True)`` for interaction IDs,
poll ``interactions.get`` by ID to terminal state, and retrieve base64 video inline in the last ``model_output`` step. AI Studio/Vertex share
SDK calls, differing only in client credentials/hosts (:class:`GeminiVideoBackend` construction). Official documentation entry points:
``docs/api-docs/providers/gemini-aistudio.md`` and ``gemini-vertex.md``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any, ClassVar

import httpx

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.backend_runtime import (
    faststart_video_artifact,
    is_retryable_http_status,
    poll_with_retry,
    should_retry_poll,
)
from lib.backends.providers import PROVIDER_GEMINI
from lib.backends.video_backends.gemini import GeminiVideoBackend, write_atomically
from lib.backends.video_backends.metering import video_usage
from lib.billing.metering import Usage
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.logging_utils import format_kwargs_for_log

logger = logging.getLogger(__name__)

#: Still-generating states; all others are terminal, with non-``completed`` terminals treated as failures.
_RUNNING_STATUSES = frozenset({"in_progress", "queued"})

#: Output video tokens per second by resolution tier (Gemini API pricing); estimate server-default 720p if resolution is unsent.
_VIDEO_TOKENS_PER_SECOND = {"360p": 1931, "720p": 5792, "1080p": 8688, "4k": 17376}
_DEFAULT_RESOLUTION = "720p"

_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class GeminiInteractionsVideoBackend(GeminiVideoBackend):
    """Generate Gemini Omni videos through Interactions API with AI Studio credentials.

    Output always has audio: no request audio switch, only desired-sound prompt descriptions. Images have no role markers; ``video_config.task``
    and prompts distinguish first/last frames from references, so they cannot mix in one request and lone last frames cannot be sent.
    """

    audio_track: ClassVar[VideoAudioMode] = VideoAudioMode.ALWAYS_ON

    @classmethod
    def video_capabilities_for_model(cls, model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """Channel implements text, first-frame, last-frame, and reference-image routes; entries provide model-specific reference counts."""
        del model, entry
        return VideoCapabilities(last_frame=True, max_reference_images=None, audio_track=cls.audio_track)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._video_model)

    @staticmethod
    def estimate_video_tokens(*, resolution: str | None, seconds: int) -> int | None:
        """Convert output duration through resolution-tier tokens per second; ``None`` for unknown/uncomputable tiers."""
        rate = _VIDEO_TOKENS_PER_SECOND.get((resolution or _DEFAULT_RESOLUTION).lower())
        return None if rate is None else rate * seconds

    @staticmethod
    def metered_usage(
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """This request's measurements (:mod:`lib.backends.video_backends.metering`), independent of model names/entries.

        Settle video tokens from provider-reported output video tokens (``result.usage_tokens``); estimate from resolution-tier tokens per second.
        Output always has audio, fixing the audio dimension to enabled.
        """
        del model, entry
        tokens = (
            result.usage_tokens
            if result is not None
            else GeminiInteractionsVideoBackend.estimate_video_tokens(
                resolution=request.resolution, seconds=request.duration_seconds
            )
        )
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=request.resolution,
            audio=True,
            video_tokens=tokens,
        )

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Submit interactions, poll to terminal states, and retrieve outputs; SDK retries submission failures by status."""
        self._reject_unsendable(request)
        interaction = await self._create_interaction(request)
        interaction_id = getattr(interaction, "id", None)
        if not interaction_id:
            # Without IDs, provider_job_id cannot persist; process interruption would cause resubmission/duplicate billing.
            raise RuntimeError("Gemini Interactions 提交成功但未返回 interaction ID，无法持久化 provider_job_id")
        await self._persist_provider_job_id(
            request, interaction_id, provider=PROVIDER_GEMINI, endpoint=self._request_base_url
        )
        done = await self._poll_interaction(interaction, request)
        return await self._collect_interaction(done, request)

    async def _resume_on_current_client(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Poll interaction IDs to terminal state and retrieve outputs without resubmission.

        Query 404 means expiry (server deleted records under retention policy); retrieval failures after completion propagate unchanged.
        """
        try:
            interaction = await self._get_interaction(job_id)
            done = await self._poll_interaction(interaction, request)
        except Exception as exc:
            if _interactions_status_code(exc) == 404:
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_GEMINI) from exc
            raise
        return await self._collect_interaction(done, request)

    def _reject_unsendable(self, request: VideoGenerationRequest) -> None:
        """Reject inputs unrepresentable in request bodies before billing, never silently discard (including user-relaxed capability overrides)."""
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._video_model)
        if not request.generate_audio:
            raise VideoCapabilityError("video_audio_switch_not_supported", provider=self.name, model=self._video_model)
        if request.reference_images and (request.start_image or request.end_image):
            raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._video_model)
        if request.end_image and not request.start_image:
            raise VideoCapabilityError("video_end_image_requires_start_image", model=self._video_model)

    async def _create_interaction(self, request: VideoGenerationRequest) -> Any:
        if self._rate_limiter:
            await self._rate_limiter.acquire_async(self._video_model)

        if request.reference_images:
            images, task = list(request.reference_images), "reference_to_video"
        elif request.start_image:
            images = [request.start_image] + ([request.end_image] if request.end_image else [])
            task = "image_to_video"
        else:
            images, task = [], "text_to_video"
        parts: list[dict[str, Any]] = list(await asyncio.gather(*[asyncio.to_thread(_image_part, i) for i in images]))
        # Callers inject textual negative prompts consistently across channels; text follows images.
        parts.append({"type": "text", "text": request.prompt})

        response_format: dict[str, Any] = {
            "type": "video",
            "aspect_ratio": request.aspect_ratio,
            "duration": f"{request.duration_seconds}s",
        }
        if request.resolution is not None:
            response_format["resolution"] = request.resolution

        logger.info(
            "调用 %s Interactions 视频 payload=%s",
            self.name,
            format_kwargs_for_log(
                {
                    "model": self._video_model,
                    "prompt": request.prompt,
                    "task": task,
                    "response_format": response_format,
                    "image_count": len(images),
                }
            ),
        )
        await self._refresh_vertex_token()
        interaction = await self._client.aio.interactions.create(
            model=self._video_model,
            input=parts,
            response_format=response_format,
            generation_config={"video_config": {"task": task}},
            background=True,
        )
        logger.info("Interactions 视频已提交, interaction=%s", getattr(interaction, "id", "unknown"))
        return interaction

    async def _refresh_vertex_token(self) -> None:
        """Refresh expired Vertex access tokens in threads.

        Async Interactions clients obtain tokens synchronously; expiry would trigger OAuth refresh on event-loop threads.
        Refresh in threads first so SDK sees valid tokens. AI Studio uses API Keys, without tokens.
        """
        credentials = self._credentials
        if credentials is None or credentials.valid:
            return
        from google.auth.transport.requests import Request

        await asyncio.to_thread(credentials.refresh, Request())

    async def _get_interaction(self, interaction_id: str) -> Any:
        await self._refresh_vertex_token()
        return await self._client.aio.interactions.get(interaction_id)

    async def _poll_interaction(self, interaction: Any, request: VideoGenerationRequest) -> Any:
        """Poll until terminal; terminals other than ``completed`` raise ``RuntimeError``; retry transient errors for the individual query only."""
        interaction_id = interaction.id
        if interaction.status in _RUNNING_STATUSES:
            interaction = await poll_with_retry(
                poll_fn=lambda: self._get_interaction(interaction_id),
                is_done=lambda it: it.status not in _RUNNING_STATUSES,
                is_failed=_failure_message,
                max_wait=request.poll_timeout_seconds,
                label="Gemini Interactions",
                retry_if=_should_retry_interactions_poll,
                on_progress=lambda _it, elapsed: logger.info(
                    "视频生成中... 已等待 %.0f 秒 (interaction=%s)", elapsed, interaction_id
                ),
            )
        failure = _failure_message(interaction)
        if failure is not None:
            raise RuntimeError(failure)
        return interaction

    async def _collect_interaction(self, interaction: Any, request: VideoGenerationRequest) -> VideoGenerationResult:
        video = interaction.output_video
        data = getattr(video, "data", None)
        if not data:
            logger.error("Interactions 视频完成但没有成片数据: interaction=%s", interaction.id)
            raise RuntimeError("视频生成失败: API 未返回视频数据")

        await run_noninterruptible_sync(request.output_path.parent.mkdir, parents=True, exist_ok=True)
        # Bytes are already in memory: decoding/writing is noninterruptible, persisted through .part then rename.
        await run_noninterruptible_sync(
            write_atomically, request.output_path, lambda target: target.write_bytes(base64.b64decode(data))
        )
        await faststart_video_artifact(request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_GEMINI,
            model=self._video_model,
            duration_seconds=request.duration_seconds,
            usage_tokens=_output_video_tokens(interaction),
            generate_audio=True,
        )


class VertexInteractionsVideoBackend(GeminiInteractionsVideoBackend):
    """Gemini Omni through Interactions API with Vertex service accounts; output likewise always has audio."""

    default_backend_type = "vertex"


def _image_part(path: Path) -> dict[str, Any]:
    image_bytes = Path(path).read_bytes()
    return {
        "type": "image",
        "data": base64.b64encode(image_bytes).decode("ascii"),
        "mime_type": _IMAGE_MIME_TYPES.get(Path(path).suffix.lower(), "image/png"),
    }


def _failure_message(interaction: Any) -> str | None:
    status = interaction.status
    if status in _RUNNING_STATUSES or status == "completed":
        return None
    errors = [f"{getattr(e, 'code', '')}: {getattr(e, 'message', '')}" for e in (interaction.errors or [])]
    detail = "; ".join(errors) if errors else "无错误详情"
    return f"视频生成失败: interaction {interaction.id} 状态为 {status}（{detail}）"


def _output_video_tokens(interaction: Any) -> int | None:
    """Provider-reported output video tokens; use total output tokens without modality splits."""
    usage = interaction.usage
    if usage is None:
        return None
    for item in usage.output_tokens_by_modality or ():
        if item.modality == "video" and item.tokens is not None:
            return item.tokens
    return usage.total_output_tokens


def _interactions_status_code(exc: BaseException) -> int | None:
    """HTTP status codes on Interactions SDK errors.

    These are not ``google.genai.errors.APIError``: codes live in ``status_code`` (``code`` for Veo),
    and error classes are not public, so read attributes.
    """
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _should_retry_interactions_poll(exc: Exception) -> bool:
    """Use status predicates when present (never retry 404; resume recognizes expiry), otherwise idempotent polling rules.

    SDK wraps httpx transport errors in connection classes, storing originals in ``__cause__``; classify original errors.
    """
    status = _interactions_status_code(exc)
    if status is not None:
        return is_retryable_http_status(status)
    cause = exc.__cause__
    if isinstance(cause, httpx.HTTPError):
        return should_retry_poll(cause)
    return should_retry_poll(exc)
