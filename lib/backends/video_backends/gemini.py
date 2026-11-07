"""GeminiVideoBackend: video-generation logic extracted from GeminiClient."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Buffer, Callable
from pathlib import Path
from typing import Any, BinaryIO, ClassVar

from PIL import Image

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
    ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS,
    ProviderJobIdPersistenceMixin,
    faststart_video_artifact,
    is_retryable_http_status,
    poll_with_retry,
    should_retry_poll,
    with_artifact_retry,
)
from lib.backends.gemini_shared import (
    GEMINI_DEFAULT_VIDEO_MODEL,
    VERTEX_SCOPES,
    RateLimiter,
    gemini_http_options,
    get_shared_rate_limiter,
    resolve_gemini_api_key,
)
from lib.backends.providers import PROVIDER_GEMINI
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import EntryBoundCapabilities
from lib.billing.metering import Usage
from lib.config.system_config import resolve_vertex_credentials_path
from lib.config.url_utils import normalize_base_url
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

#: Per-operation video-download I/O timeout, matching ``download_video`` defaults: stalled connection/reads raise for retry budgets.
_DOWNLOAD_IO_TIMEOUT_SECONDS = 120.0
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
#: SDK-default AI Studio host without base_url configuration, compared with submission hosts for resume replay.
_AISTUDIO_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/"


class GeminiVideoBackend(EntryBoundCapabilities, ProviderJobIdPersistenceMixin):
    """Gemini (Veo) video-generation backend."""

    audio_track: ClassVar[VideoAudioMode] = VideoAudioMode.ALWAYS_ON
    default_backend_type: ClassVar[str] = "aistudio"

    def __init__(
        self,
        *,
        backend_type: str | None = None,
        api_key: str | None = None,
        rate_limiter: RateLimiter | None = None,
        video_model: str | None = None,
        base_url: str | None = None,
        credentials_path: str | None = None,
        transport_layer: TransportLayer | None = None,
    ):
        from google import genai as _genai
        from google.genai import types as _types

        self._types = _types
        self._rate_limiter = rate_limiter or get_shared_rate_limiter()
        self._backend_type = (backend_type or self.default_backend_type).strip().lower()
        self._credentials = None
        self._project_id = None
        self._api_key: str | None = None
        self._base_url: str | None = None
        self._transport_layer = transport_layer
        self._http_options: _types.HttpOptions | _types.HttpOptionsDict | None = None

        self._video_model = video_model or GEMINI_DEFAULT_VIDEO_MODEL

        if self._backend_type == "vertex":
            import json as json_module

            from google.oauth2 import service_account

            credentials_file = Path(credentials_path) if credentials_path else resolve_vertex_credentials_path()
            if credentials_file is None:
                raise ValueError("未找到 Vertex AI 凭证文件")

            with open(credentials_file, encoding="utf-8") as f:
                creds_data = json_module.load(f)
            self._project_id = creds_data.get("project_id")

            self._credentials = service_account.Credentials.from_service_account_file(
                str(credentials_file), scopes=VERTEX_SCOPES
            )

            self._client = _genai.Client(
                vertexai=True,
                project=self._project_id,
                location="global",
                credentials=self._credentials,
            )
        else:
            self._api_key = resolve_gemini_api_key(api_key)
            self._base_url = normalize_base_url(base_url)
            self._http_options = gemini_http_options(self._base_url, transport_layer)
            self._client = _genai.Client(api_key=self._api_key, http_options=self._http_options)

    async def _close_injected_http_clients(self) -> None:
        # SDK close/aclose skips externally supplied httpx clients.
        options = self._http_options
        if self._transport_layer is None or not isinstance(options, self._types.HttpOptions):
            return
        if options.httpx_async_client is not None:
            await options.httpx_async_client.aclose()
        if options.httpx_client is not None:
            options.httpx_client.close()

    @property
    def name(self) -> str:
        return f"gemini-{self._backend_type}"

    @property
    def model(self) -> str:
        return self._video_model

    @staticmethod
    def metered_usage(
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """This request's measurements (:mod:`lib.backends.video_backends.metering`).

        Audio follows actual requests: Vertex uses sent ``generate_audio`` (including user-added/audio-relaxed models); AI Studio
        cannot send silence, rejecting ``generate_audio=False`` before submission (``generate``), so accepted requests are audible.
        Callers construct estimates with execution-effective switches (uncontrollable audio models use audible, ``VideoRequestCostFacts``).
        Settlement uses ``result.generate_audio`` (``_collect_video``). Every model meters the same request fields, without model/entry distinctions.
        """
        del model, entry
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=request.resolution,
            audio=request.generate_audio,
        )

    @classmethod
    def video_capabilities_for_model(cls, model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """AI Studio lacks audio switches; Vertex endpoints can send generate_audio."""
        return VideoCapabilities(last_frame=True, max_reference_images=None, audio_track=cls.audio_track)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        caps = self.video_capabilities_for_model(self._video_model)
        if self._backend_type == "vertex":
            from dataclasses import replace

            return replace(caps, audio_track=VideoAudioMode.CONTROLLABLE)
        return caps

    @property
    def _request_base_url(self) -> str | None:
        """Actual AI Studio host (SDK default when unconfigured); Vertex hosts do not vary by user configuration, returning None."""
        if self._backend_type == "vertex":
            return None
        return self._base_url or _AISTUDIO_DEFAULT_BASE_URL

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Generate videos with separate creation/polling retry scopes, avoiding task recreation for transient failures."""
        # No reference-audio body fields; user-relaxed capabilities still cannot send them. Reject before billing, never silently discard.
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._video_model)
        # AI Studio lacks audio switches, likewise rejecting silent requests; already-submitted resume_video tasks are exempt.
        if not request.generate_audio and self._backend_type != "vertex":
            raise VideoCapabilityError("video_audio_switch_not_supported", provider=self.name, model=self._video_model)
        operation = await self._create_task(request)
        op_name = getattr(operation, "name", None)
        if not op_name:
            # Fail fast: missing operation.name means successful submission without persistable provider_job_id.
            # After interruption, orphan handlers would [restart_lost] into resubmission, exactly the duplicate-billing
            # case to prevent. Raise so worker finally marks failed instead of silently continuing polling.
            raise RuntimeError("Gemini 提交成功但未返回 operation.name，无法持久化 provider_job_id")
        # Persist actual request hosts too: resume replays them, otherwise changed base_url would query old operations on new hosts.
        await self._persist_provider_job_id(request, op_name, provider=PROVIDER_GEMINI, endpoint=self._request_base_url)
        done_operation = await self._poll_until_done(operation, request)
        return await self._collect_video(done_operation, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Resume submitted Gemini tasks by polling task IDs and retrieving outputs (:meth:`_resume_on_current_client`).

        Expiry covers only operation queries by job_id: remote LRO GC returns NOT_FOUND on initial
        ``operations.get`` and mid-poll queries, classified as ResumeExpiredError so worker finally applies
        ``[resume_expired]``. Retrieval failures after completion, such as artifact URL 404,
        mean remote success without fetched outputs; propagate retrieval failures unchanged, never as expiry.

        Operations are queryable only on submission hosts: AI Studio resume across host configuration changes creates an instance
        bound to the submission host with the same credentials for polling/retrieval, closing its pool afterward.
        """
        submitted = normalize_base_url(request.submitted_base_url)
        if submitted and self._backend_type != "vertex" and submitted != self._request_base_url:
            replay = type(self)(
                backend_type=self._backend_type,
                api_key=self._api_key,
                rate_limiter=self._rate_limiter,
                video_model=self._video_model,
                base_url=submitted,
                transport_layer=self._transport_layer,
            )
            try:
                return await replay.resume_video(job_id, request)
            finally:
                await replay._client.aio.aclose()
                replay._client.close()
                await replay._close_injected_http_clients()
        return await self._resume_on_current_client(job_id, request)

    async def _resume_on_current_client(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Poll/retrieve ``job_id`` on current clients; :meth:`resume_video` handles submission-host replay.

        Reconstruct GenerateVideosOperation from name: Operation is abstract and cannot instantiate; concrete LRO subclasses
        accept dicts through Pydantic v2 ``model_validate`` (bypassing pyright's incomplete multiple-inheritance field inference).
        """
        op = self._types.GenerateVideosOperation.model_validate({"name": job_id, "done": False})
        try:
            refreshed = await self._client.aio.operations.get(op)
            done_operation = await self._poll_until_done(refreshed, request)
        except Exception as exc:
            if _is_gemini_not_found(exc):
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_GEMINI) from exc
            raise
        return await self._collect_video(done_operation, request)

    @with_retry_async()
    async def _create_task(self, request: VideoGenerationRequest) -> Any:
        """Create Gemini video-generation tasks with retry protection."""
        # 1. Rate limiting.
        if self._rate_limiter:
            await self._rate_limiter.acquire_async(self._video_model)

        # 2. Pass duration unchanged (model supported_durations validates it; no backend bucketing).
        duration_str = str(request.duration_seconds)

        # 3. Build config.
        # Negative prompts bypass parameter channels: callers inject text into request.prompt tails
        # through lib.prompts.prompt_utils.render_storyboard_video_prompt storyboard/video templates, consistently across backends.
        config_params: dict = {
            "aspect_ratio": request.aspect_ratio,
            "duration_seconds": duration_str,
        }
        if request.resolution is not None:
            config_params["resolution"] = request.resolution
        if self._backend_type == "vertex":
            config_params["generate_audio"] = request.generate_audio

        # end_image → last_frame (frame interpolation).
        if request.end_image is not None:
            config_params["last_frame"] = await asyncio.to_thread(self._prepare_image_param, request.end_image)

        # reference_images → reference_images (reference lists, type=ASSET).
        if request.reference_images:
            prepared_refs = await asyncio.gather(
                *[asyncio.to_thread(self._prepare_image_param, img) for img in request.reference_images]
            )
            config_params["reference_images"] = [
                self._types.VideoGenerationReferenceImage(
                    image=prepared,
                    reference_type=self._types.VideoGenerationReferenceType.ASSET,
                )
                for prepared in prepared_refs
            ]

        config = self._types.GenerateVideosConfig(**config_params)

        # 4. Prepare source (prompt + optional start frame).
        image_param = (
            await asyncio.to_thread(self._prepare_image_param, request.start_image) if request.start_image else None
        )
        source = self._types.GenerateVideosSource(prompt=request.prompt, image=image_param)

        # 5. Call API.
        logger.info(
            "调用 %s 视频 SDK payload=%s",
            self.name,
            format_kwargs_for_log(
                {
                    "model": self._video_model,
                    "prompt": request.prompt,
                    "config": config_params,
                    "has_start_image": request.start_image is not None,
                    "reference_image_count": len(request.reference_images) if request.reference_images else 0,
                }
            ),
        )
        operation = await self._client.aio.models.generate_videos(model=self._video_model, source=source, config=config)
        op_name = getattr(operation, "name", "unknown")
        logger.info("视频生成已提交, operation=%s", op_name)
        return operation

    async def _poll_until_done(self, operation: Any, request: VideoGenerationRequest) -> Any:
        """Poll to completion, returning completed operations; retry transient failures for individual queries only."""
        op_name = getattr(operation, "name", "unknown")
        logger.info("开始轮询 operation=%s ...", op_name)

        if not operation.done:
            operation = await poll_with_retry(
                # SDK queries operation.name and returns new objects; closures need only capture initial operations.
                poll_fn=lambda: self._client.aio.operations.get(operation),
                is_done=lambda op: op.done,
                is_failed=lambda op: None,  # Gemini checks failures after polling completes.
                max_wait=request.poll_timeout_seconds,
                label="Gemini",
                retry_if=_should_retry_gemini_poll,
                on_progress=lambda op, elapsed: logger.info(
                    "视频生成中... 已等待 %.0f 秒 (operation=%s)", elapsed, op_name
                ),
            )

        logger.info("视频生成完成 (operation=%s)", op_name)
        return operation

    async def _collect_video(self, operation: Any, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Validate completed-operation outputs, retrieve/persist and assemble results."""
        op_name = getattr(operation, "name", "unknown")
        if not operation.response or not operation.response.generated_videos:
            error_detail = getattr(operation, "error", None)
            metadata = getattr(operation, "metadata", None)
            logger.error(
                "视频生成返回空结果: operation=%s, error=%s, metadata=%s",
                op_name,
                error_detail,
                metadata,
            )
            if error_detail:
                raise RuntimeError(f"视频生成失败: {error_detail}")
            raise RuntimeError("视频生成失败: API 返回空结果")

        # Extract/download videos.
        generated_video = operation.response.generated_videos[0]
        video_ref = generated_video.video
        video_uri = video_ref.uri if video_ref else None

        await run_noninterruptible_sync(request.output_path.parent.mkdir, parents=True, exist_ok=True)
        await self._download_video_with_retry(video_ref, request.output_path)
        await faststart_video_artifact(request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_GEMINI,
            model=self._video_model,
            duration_seconds=request.duration_seconds,
            video_uri=video_uri,
            generate_audio=request.generate_audio if self._backend_type == "vertex" else True,
        )

    # ------------------------------------------------------------------
    # Internal helpers extracted from GeminiClient.
    # ------------------------------------------------------------------

    def _prepare_image_param(self, image: str | Path | Image.Image | None):
        """Prepare API image parameters, extracted from GeminiClient."""
        if image is None:
            return None

        mime_type_png = "image/png"

        if isinstance(image, (str, Path)):
            with open(image, "rb") as f:
                image_bytes = f.read()
            suffix = Path(image).suffix.lower()
            mime_types = {
                ".png": mime_type_png,
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }
            mime_type = mime_types.get(suffix, mime_type_png)
            return self._types.Image(image_bytes=image_bytes, mime_type=mime_type)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
        return self._types.Image(image_bytes=image_bytes, mime_type=mime_type_png)

    async def _download_video_with_retry(self, video_ref, output_path: Path) -> None:
        """Download videos under shared artifact retry budgets.

        SDK retrieval does not raise ``HTTPStatusError``, so use ``retryable_errors`` predicates.
        """
        if self._backend_type == "vertex" and video_ref and getattr(video_ref, "video_bytes", None):
            # Existing bytes only need persistence, outside network retrieval timeout/retry budgets.
            await run_noninterruptible_sync(
                write_atomically, output_path, lambda target: target.write_bytes(video_ref.video_bytes)
            )
            return
        # Threads write .part then replace outputs: cancellation waits for writes/outcomes before propagation, avoiding writes after caller cleanup.
        # Outer asyncio.timeout cannot terminate threads; threads finish against the same budget deadline themselves.
        deadline = time.monotonic() + ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS
        await with_artifact_retry(
            lambda: run_sync_transaction(self._download_video, video_ref, output_path, deadline=deadline),
            label="Gemini",
            retry_if=None,
            max_wait=ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS,
        )

    def _download_video(self, video_ref, output_path: Path, *, deadline: float) -> None:
        """Download videos locally, extracted from GeminiClient.

        All three persistence routes use ``write_atomically``: write same-directory ``.part``, then atomic rename,
        preventing failed fragments at output_path being mistaken for finished videos and preserving existing outputs.
        Both network routes set I/O timeouts and compare each chunk to ``deadline`` (``time.monotonic()`` time),
        raising ``TimeoutError`` at expiry so threads cannot outlive retrieval budgets.
        """
        if self._backend_type == "vertex":
            if video_ref and hasattr(video_ref, "video_bytes") and video_ref.video_bytes:
                video_bytes = video_ref.video_bytes
                write_atomically(output_path, lambda target: target.write_bytes(video_bytes))
            elif video_ref and hasattr(video_ref, "uri") and video_ref.uri:
                uri = video_ref.uri
                write_atomically(output_path, lambda target: _fetch_uri(uri, target, deadline))
            else:
                raise RuntimeError("视频生成成功但无法获取视频数据")
        else:
            # AI Studio files.download with destination writes chunks directly without loading entire videos into memory.
            def write(target: Path) -> None:
                timeout_ms = int(_io_timeout(deadline) * 1000)
                with open(target, "wb") as handle:
                    self._client.files.download(
                        file=video_ref,
                        destination=_DeadlineWriter(handle, deadline),
                        config={"http_options": {"timeout": timeout_ms}},
                    )

            write_atomically(output_path, write)


class _DeadlineWriter(io.RawIOBase):
    """File wrapper checking wall-clock deadlines before writes, raising ``TimeoutError`` after expiry.

    I/O timeouts bound individual stalled reads; per-chunk deadline checks terminate continuously slow downloads.
    Inherit ``RawIOBase`` to satisfy SDK ``destination`` ``IOBase`` typing.
    """

    def __init__(self, handle: BinaryIO, deadline: float) -> None:
        super().__init__()
        self._handle = handle
        self._deadline = deadline
        self.written = 0

    def writable(self) -> bool:
        return True

    def write(self, b: Buffer, /) -> int:
        if time.monotonic() >= self._deadline:
            raise TimeoutError("Gemini 成片下载超出取件预算")
        written = self._handle.write(b)
        self.written += written
        return written


def _io_timeout(deadline: float) -> float:
    """Per-network-operation timeout: bounded by I/O maximum and deadline; raise ``TimeoutError`` immediately after expiry."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Gemini 成片下载超出取件预算")
    return min(_DOWNLOAD_IO_TIMEOUT_SECONDS, remaining)


def _fetch_uri(uri: str, target: Path, deadline: float) -> None:
    """Stream ``uri`` into ``target`` with I/O timeouts/deadline checks.

    ``urlopen`` raises ``HTTPError`` for HTTP failures; bodies shorter than Content-Length raise
    ``ContentTooShortError``, preventing truncated files being accepted as finished videos.
    """
    with urllib.request.urlopen(uri, timeout=_io_timeout(deadline)) as response, open(target, "wb") as handle:
        sink = _DeadlineWriter(handle, deadline)
        shutil.copyfileobj(response, sink, _DOWNLOAD_CHUNK_BYTES)
        expected = response.headers.get("Content-Length")
        if expected is not None and sink.written < int(expected):
            raise urllib.error.ContentTooShortError(
                f"retrieval incomplete: got only {sink.written} out of {expected} bytes",
                (str(target), response.headers),
            )


def write_atomically(output_path: Path, write: Callable[[Path], object]) -> None:
    """Have ``write`` target same-directory ``<name>.part``, then ``os.replace`` it into output_path on success.

    Writers open destinations directly with "wb", leaving truncated files on failure; ``.part``
    protects output_path from failed overwrites, cleaning fragments on ``BaseException``.
    """
    partial_path = output_path.with_name(f"{output_path.name}.part")
    try:
        write(partial_path)
        os.replace(partial_path, output_path)
    except BaseException:
        partial_path.unlink(missing_ok=True)  # hard-delete: temp -- atomic-write .part file
        raise


def _should_retry_gemini_poll(exc: Exception) -> bool:
    """Gemini polling predicates: SDK ``APIError`` uses HTTP status; other exceptions use idempotent polling rules.

    ``str(APIError)`` includes entire bodies; operation-name digits may contain "500" / "429". Substring fallback would
    retry 404 until budget exhaustion, preventing resume from recognizing reclaimed operations.
    """
    from google.genai import errors as genai_errors

    if isinstance(exc, genai_errors.APIError):
        return is_retryable_http_status(exc.code)
    return should_retry_poll(exc)


def _is_gemini_not_found(exc: BaseException) -> bool:
    """Recognize nonexistent/expired Gemini operations.get responses.

    Accept SDK ``APIError`` HTTP 404 only: "not found" / "expired" in messages can arise outside
    operation lifetime (transport failures/upstream reasons); substring checks would incorrectly classify them
    as expiry. INVALID_ARGUMENT (400) is not expiry either: Gemini uses it for invalid parameters such as malformed
    operation names; classifying it as expiry would silently swallow client bugs as ghost tasks.
    """
    from google.genai import errors as genai_errors

    return isinstance(exc, genai_errors.APIError) and exc.code == 404


class VertexVideoBackend(GeminiVideoBackend):
    """Veo through Vertex service accounts, with audio control."""

    audio_track = VideoAudioMode.CONTROLLABLE
    default_backend_type = "vertex"
