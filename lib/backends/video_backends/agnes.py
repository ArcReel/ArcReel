"""AgnesVideoBackend: Agnes video generation with async polling and resume.

v2.0 submits ``POST /v1/videos``, polling ``GET /v1/videos/{task_id}``; requests describe output with WxH / frame counts,
with bare base64 media inputs. 2.5 shares submission endpoints but uses ``mode`` + seconds strings + resolution tiers,
Data URI inputs, and ``GET /agnesapi?video_id=...&model_name=...`` polling.

Both contracts determine terminal states through ``status``, downloading from top-level ``url`` or query response URLs; 2.5 obtains
actual billed duration from response ``seconds``. Keyframe/multi-image mapping follows single-channel constraints: no images means text-to-video;
first/last frames use ``keyframe``, references use ``reference``, never mixing references with frames.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit

import httpx

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.aspect_size import VIDEO_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from arcreel_market_core.video_backend_contract import (
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.agnes_shared import agnes_base_url, agnes_headers, agnes_host, resolve_agnes_api_key
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import (
    ProviderJobIdPersistenceMixin,
    download_resumable_video,
    poll_with_retry,
    recording_poll,
    resume_expiry_gate,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.backends.image_backends.base import image_to_base64_data_uri
from lib.backends.providers import PROVIDER_AGNES
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import EntryBoundCapabilities
from lib.backends.video_capabilities import entry_video_facts
from lib.billing.metering import Usage
from lib.catalog import query as catalog
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    with_retry_async,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "agnes-video-2.5-flash"

_VIDEOS_ENDPOINT = "/videos"
# Completed-video query at gateway root, outside /v1, queried by video_id.
_VIDEO_QUERY_ENDPOINT = "/agnesapi"

# Fixed 24 fps; num_frames must be 8n+1. Round seconds×fps then align to nearest 8n+1 without truncation:
# upstream rejects beyond-limit frames rather than silently shortening outputs. 1–3s yields below 81 frames (25/49/73),
# legal under documentation.
_FPS = 24
_FRAME_STEP = 8

_V25_ASPECT_RATIOS = frozenset({"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"})
_V25_RESOLUTIONS = {"720P": "720P", "1080P": "1080P", "2K": "2K"}
# v2.0 reference capacity (multi-image subjects) is a channel request-structure fact. 2.5 imposes no channel count limit;
# entries record flash/full-model capacities (docs/adr/0104).
_MAX_REFERENCE_IMAGES = 4

# Dimensions divisible by 8, long edge capped at conservative 1920, covering upstream normalized 480p/720p/1080p tiers.
# Without resolution, use 720p short edges. Pixel caps unverified in Agnes console must not be hardcoded as established facts.
_VIDEO_ROUND_TO = 8
_MAX_LONG_EDGE = 1920

# Submission timeout ~300s covers long upstream contention, preventing retryable busy responses becoming terminal ambiguous ReadTimeout.
_SUBMIT_TIMEOUT_SECONDS = 300.0
# Polling/downloads use shorter timeouts, as idempotent GET usually returns within seconds.
_POLL_HTTP_TIMEOUT_SECONDS = 60.0

_KEYFRAMES_MODE = "keyframes"
_V25_TEXT_MODE = "text"
_V25_KEYFRAME_MODE = "keyframe"
_V25_REFERENCE_MODE = "reference"

# Terminal failures include documented failed plus error / cancelled / canceled, preventing nonstandard failures
# from being polled as ongoing until timeout.
_FAILED_STATUSES = ("failed", "error", "cancelled", "canceled")

# Safe scalar log allowlist; never log base64 inside image / extra_body.
_SAFE_LOG_KEYS = (
    "model",
    "height",
    "width",
    "num_frames",
    "frame_rate",
    "seconds",
    "size",
    "aspect_ratio",
    "mode",
    "seed",
)

# Authoritative completed-video URL fields, by priority; top-level/metadata equal, checking top-level first.
_PRIMARY_URL_FIELDS = ("url", "video_url")

# Remix source video ID is not semantically a URL: non-URL shapes are never download addresses. Retain only for gateways
# returning actual URLs there; lower priority than _PRIMARY_URL_FIELDS, so any authoritative top-level/metadata URL
# precedes it, never allowing it to displace the actual download address.
_COMPAT_URL_FIELD = "remixed_from_video_id"


def _looks_like_url(value: str) -> bool:
    """Coarse URL validation: http/https scheme + nonempty netloc. Distinguish semantically non-URL fields such as remixed_from_video_id
    from download addresses, preventing remix source IDs from being downloaded as URLs.
    """
    if not value:
        return False
    parsed = urlsplit(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _first_url_field(body: dict) -> str | None:
    """Find URL-shaped video fields: prefer _PRIMARY_URL_FIELDS, then _COMPAT_URL_FIELD;
    within each priority, top-level and ``metadata`` (gateway queries put URLs in metadata.url) have equal authority,
    checking top-level first. None without matches.

    All _COMPAT_URL_FIELD fallback checks must follow _PRIMARY_URL_FIELDS: otherwise top-level compatibility fields
    could precede authoritative metadata URLs and be mistaken for download addresses.
    """
    metadata = body.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    for key in _PRIMARY_URL_FIELDS:
        for source in (body, metadata):
            value = source.get(key)
            if isinstance(value, str) and _looks_like_url(value):
                return value

    for source in (body, metadata):
        value = source.get(_COMPAT_URL_FIELD)
        if isinstance(value, str) and _looks_like_url(value):
            return value

    return None


def _duration_to_num_frames(duration_seconds: int) -> int:
    """Seconds → num_frames: round seconds × fps, then align to nearest ``8n+1``."""
    target = max(1, duration_seconds) * _FPS
    n = round((target - 1) / _FRAME_STEP)
    return max(1, _FRAME_STEP * n + 1)


def _resolve_size(resolution: str | None, aspect_ratio: str) -> tuple[int, int]:
    """Ratio first, clarity second: resolution gives short edges (tiers/custom/None defaults to 720p),
    aspect_ratio gives exact proportions, dimensions divisible by 8, long edge capped at 1920. Return (width, height).
    """
    short = resolution_to_short_edge(resolution, tier_map=VIDEO_TIER_SHORT_EDGE)
    return aspect_size(aspect_ratio, short, round_to=_VIDEO_ROUND_TO, max_long_edge=_MAX_LONG_EDGE)


def _image_to_bare_base64(image_path: Path) -> str:
    """Local images → **bare base64** strings without ``data:`` prefixes.

    Agnes video decodes entire strings as base64; ``data:`` prefixes cause generation-time padding errors, so do not reuse
    common data-URI helpers (image endpoints accept data URIs, video endpoints do not; never mix them).
    """
    return base64.b64encode(image_path.read_bytes()).decode("ascii")


def _image_to_data_uri(image_path: Path) -> str:
    """Local images → ``data:image/...;base64,...``; 2.5 consistently uses this media form."""
    return image_to_base64_data_uri(image_path)


@dataclass(frozen=True)
class AgnesVideoEndpoint:
    """Request contract shared by submission, polling, and audio."""

    v25: bool


def _safe_body_for_log(body: dict) -> dict:
    """Safe logs: allowlisted scalars + prompt lengths + image counts, never base64."""
    view: dict = {key: body[key] for key in _SAFE_LOG_KEYS if key in body}
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        view["prompt_len"] = len(prompt)
    if body.get("image"):
        view["image"] = "<start_frame>"
    if body.get("first_frame"):
        view["first_frame"] = "<first_frame>"
    if body.get("last_frame"):
        view["last_frame"] = "<last_frame>"
    images = body.get("images")
    if isinstance(images, list):
        view["images"] = f"<{len(images)} ref>"
    extra = body.get("extra_body")
    if isinstance(extra, dict) and isinstance(extra.get("image"), list):
        mode = extra.get("mode")
        view["extra_body"] = f"<{len(extra['image'])} img{f', mode={mode}' if mode else ''}>"
    return view


def _extract_task_id(body: dict, *, prefer_video_id: bool) -> str:
    """Extract submission task ids for polling: 2.5 polls video_id, v2.0 paths poll task_id."""
    keys = ("video_id", "task_id", "id") if prefer_video_id else ("task_id", "video_id", "id")
    for key in keys:
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    # Expose field names only, never full responses containing prompts/signed URLs, matching _safe_body_for_log.
    raise RuntimeError(f"Agnes 视频提交返回体缺少 task_id（字段: {sorted(body)}）")


def _extract_duration_seconds(final: dict, queried: dict | None, fallback: int) -> int:
    """Actual output duration from terminal polling top-level ``seconds``; fall back to second-query ``seconds`` by video_id
    (available only when completed responses contain video_id). Use request duration only if both are missing/unparseable.

    Never read ``usage.duration_seconds``: task processing time, unrelated to output duration, would corrupt
    billing/metadata.
    """
    parsed = _coerce_duration(final.get("seconds"))
    if parsed is not None:
        return parsed
    if queried is not None:
        parsed = _coerce_duration(queried.get("seconds"))
        if parsed is not None:
            return parsed
    return fallback


def _coerce_duration(value: object) -> int | None:
    """Normalize durations such as ``"10.0"`` / ``10`` to billed seconds: half-up (4.5→5, no undercount),
    returning None for nonpositive, >24h (protect DB Integer from overflow), or unparseable values so callers use request durations.
    """
    if value is None:
        return None
    try:
        decimal_value = Decimal(str(value))
        if not 0 < decimal_value <= MAX_BILLED_DURATION_SECONDS:
            return None
        return int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _failure_reason(state: dict) -> str | None:
    """Failed terminals (failed / error / cancelled / canceled) → error descriptions; otherwise None.

    Recognize more than ``failed``: otherwise other upstream failures appear ongoing, occupying workers
    until max_wait raises misleading TimeoutError. Explicit failure enumeration fails quickly.
    """
    if state.get("status") not in _FAILED_STATUSES:
        return None
    err = state.get("error")
    message = (err.get("message") or err.get("code") or "unknown") if isinstance(err, dict) else (err or "unknown")
    return f"Agnes 视频生成失败: {message}"


class AgnesVideoBackend(EntryBoundCapabilities, ProviderJobIdPersistenceMixin):
    """Agnes video backend (async submit/poll, bare base64 images, resume support)."""

    endpoint: ClassVar[AgnesVideoEndpoint] = AgnesVideoEndpoint(v25=False)
    default_model: ClassVar[str] = "agnes-video-v2.0"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = _POLL_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = resolve_agnes_api_key(api_key)
        self._base_url = agnes_base_url(base_url)
        self._host = agnes_host(base_url)
        self._model = model or self.default_model
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_AGNES

    @property
    def model(self) -> str:
        return self._model

    @classmethod
    def video_capabilities_for_model(cls, model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """Endpoints determine input slots/audio without reading model names."""
        return VideoCapabilities(
            last_frame=True,
            max_reference_images=None if cls.endpoint.v25 else _MAX_REFERENCE_IMAGES,
            audio_track=VideoAudioMode.ALWAYS_ON if cls.endpoint.v25 else VideoAudioMode.ALWAYS_OFF,
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # No reference-audio body field; user-relaxed capabilities still cannot send it. Reject before billing, never silently discard.
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)
        # Offload reads/encoding (up to 2 first/last frames or 8 references, potentially MBs) to threads,
        # preserving shared worker event-loop responsiveness like image and grok/gemini video backends.
        payload = await asyncio.to_thread(self._build_payload, request)
        logger.info(
            "调用 %s 视频 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(_safe_body_for_log(payload)),
        )
        async with artifact_http_client(timeout=self._http_timeout) as client:
            task_id = await self._create_task(client, payload, request)
            logger.info("Agnes 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            await self._persist_provider_job_id(request, task_id, provider=PROVIDER_AGNES)
            return await self._poll_and_build(client, task_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Resume submitted Agnes tasks: polling/download only, never resubmit (ADR 0007)."""
        async with artifact_http_client(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, job_id, request, is_resume=True)

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

        Both contracts derive resolution from request tiers, sending 720p if unspecified (2.5 ``size``, v2.0 short edges); settle output seconds
        from Agnes-reported duration. 2.5 outputs audio, v2.0 silence; metering/construction share endpoint declarations.
        """
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=request.resolution or "720p",
            audio=cls.endpoint.v25,
        )

    # ── request building ────────────────────────────────────────────────

    def _build_payload(self, request: VideoGenerationRequest) -> dict:
        """Build v2.0 / 2.5 requests from endpoint declarations."""
        if self.endpoint.v25:
            return self._build_v25_payload(request)
        return self._build_v20_payload(request)

    def _build_v20_payload(self, request: VideoGenerationRequest) -> dict:
        """Build v2.0 bodies, preserving existing WxH / frame count / bare base64 contracts."""
        width, height = _resolve_size(request.resolution, request.aspect_ratio)
        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "height": height,
            "width": width,
            "num_frames": _duration_to_num_frames(request.duration_seconds),
            "frame_rate": _FPS,
        }
        if request.seed is not None:
            payload["seed"] = request.seed

        reference_images = self._valid_paths(request.reference_images)
        start_image = self._single_path(request.start_image)
        end_image = self._single_path(request.end_image)

        if reference_images and (start_image is not None or end_image is not None):
            raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._model)
        if end_image is not None and start_image is None:
            raise VideoCapabilityError("video_end_image_requires_start_image", model=self._model)

        if reference_images:
            if len(reference_images) > _MAX_REFERENCE_IMAGES:
                raise VideoCapabilityError(
                    "video_reference_images_exceeded",
                    model=self._model,
                    count=len(reference_images),
                    limit=_MAX_REFERENCE_IMAGES,
                )
            payload["extra_body"] = {"image": [self._encode_reference(p) for p in reference_images]}
        elif start_image is not None and end_image is not None:
            payload["extra_body"] = {
                "image": [self._encode_start(start_image), self._encode_end(end_image)],
                "mode": _KEYFRAMES_MODE,
            }
        elif start_image is not None:
            payload["image"] = self._encode_start(start_image)

        return payload

    def _build_v25_payload(self, request: VideoGenerationRequest) -> dict:
        """Build 2.5 bodies: mode + seconds strings + resolution tiers + Data URI media."""
        self._reject_v25_aspect_ratio(request.aspect_ratio)
        size = self._normalize_v25_resolution(request.resolution)

        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "seconds": str(request.duration_seconds),
            "size": size,
            "aspect_ratio": request.aspect_ratio,
        }
        if request.seed is not None:
            payload["seed"] = request.seed

        reference_images = self._valid_paths(request.reference_images)
        start_image = self._single_path(request.start_image)
        end_image = self._single_path(request.end_image)

        if reference_images and (start_image is not None or end_image is not None):
            raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._model)
        if end_image is not None and start_image is None:
            raise VideoCapabilityError("video_end_image_requires_start_image", model=self._model)

        if reference_images:
            # Counts are model facts (flash 5, full 8, in entries), validated by gate_video_request effective capabilities before billing.
            payload["mode"] = _V25_REFERENCE_MODE
            payload["images"] = [self._encode_reference(p, data_uri=True) for p in reference_images]
        elif start_image is not None:
            payload["mode"] = _V25_KEYFRAME_MODE
            payload["first_frame"] = self._encode_start(start_image, data_uri=True)
            if end_image is not None:
                payload["last_frame"] = self._encode_end(end_image, data_uri=True)
        else:
            payload["mode"] = _V25_TEXT_MODE

        return payload

    def _normalize_v25_resolution(self, resolution: str | None) -> str:
        facts = entry_video_facts(self._catalog_entry or catalog.find_entry(self._model, media_type="video"))
        allowed = (
            {r.upper(): r.upper() for r in facts.resolutions if r.upper() in _V25_RESOLUTIONS}
            if facts.resolutions
            else _V25_RESOLUTIONS
        )
        normalized = (resolution or "720p").upper()
        if normalized not in allowed:
            raise VideoCapabilityError(
                "video_resolution_not_supported",
                model=self._model,
                resolution=resolution,
                supported=", ".join(allowed),
            )
        return allowed[normalized]

    def _reject_v25_aspect_ratio(self, aspect_ratio: str) -> None:
        if aspect_ratio not in _V25_ASPECT_RATIOS:
            raise VideoCapabilityError(
                "video_aspect_ratio_not_supported",
                model=self._model,
                aspect_ratio=aspect_ratio,
                supported=", ".join(sorted(_V25_ASPECT_RATIOS)),
            )

    @staticmethod
    def _single_path(value: str | Path | None) -> Path | None:
        """Normalize image request fields to Path; absent/empty strings/empty Paths (``Path("")`` becomes ``Path(".")``) → None."""
        if value is None:
            return None
        text = str(value)
        if not text or text == ".":
            return None
        return Path(text)

    @classmethod
    def _valid_paths(cls, values: list[Path] | None) -> list[Path]:
        """Normalize reference lists, removing empty values/Paths (``[Path(v) for v if v]`` always accepts Path and cannot filter them)."""
        return [p for v in (values or []) if (p := cls._single_path(v)) is not None]

    def _encode_start(self, path: Path, *, data_uri: bool = False) -> str:
        """Encode first frames; fail loudly for missing/unreadable inputs, never silently degrade to text-to-video."""
        return self._encode_image(
            path,
            error_code="video_start_image_unreadable",
            name=path.name or str(path),
            data_uri=data_uri,
        )

    def _encode_end(self, path: Path, *, data_uri: bool = False) -> str:
        """Encode last frames; fail loudly for missing/unreadable inputs, identifying last rather than first frames."""
        return self._encode_image(
            path,
            error_code="video_end_image_unreadable",
            name=path.name or str(path),
            data_uri=data_uri,
        )

    def _encode_reference(self, path: Path, *, data_uri: bool = False) -> str:
        """Encode references; fail loudly for missing/unreadable images, never silently discard while still billing."""
        return self._encode_image(
            path,
            error_code="video_reference_images_unreadable",
            names=path.name or str(path),
            data_uri=data_uri,
        )

    def _encode_image(self, path: Path, *, error_code: str, data_uri: bool = False, **err_params: str) -> str:
        """Encode images; fail loudly with channel error_code / parameter names when missing/unreadable."""
        if not path.is_file():
            raise VideoCapabilityError(error_code, model=self._model, **err_params)
        try:
            return _image_to_data_uri(path) if data_uri else _image_to_bare_base64(path)
        except OSError as exc:
            raise VideoCapabilityError(error_code, model=self._model, **err_params) from exc

    # ── HTTP submit / poll / download ───────────────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(
        self, client: httpx.AsyncClient, payload: dict, request: VideoGenerationRequest | None = None
    ) -> str:
        # Nonidempotent task creation + billing POST: submit_post converts ambiguous transport errors to terminal AmbiguousSubmitError,
        # preventing duplicate tasks/billing. >=400 raises HTTPStatusError for should_retry_submit status routing
        # (retry 5xx/408/429, including upstream-busy 503; deterministic 4xx fail fast). Long submit timeouts cover upstream blocking.
        resp = await submit_post(
            lambda: client.post(
                f"{self._base_url}{_VIDEOS_ENDPOINT}",
                json=payload,
                headers=agnes_headers(self._api_key),
                timeout=_SUBMIT_TIMEOUT_SECONDS,
            ),
            provider=PROVIDER_AGNES,
            request=request,
        )
        return _extract_task_id(resp.json(), prefer_video_id=self.endpoint.v25)

    async def _poll_once(self, client: httpx.AsyncClient, task_id: str) -> dict:
        resp = await client.get(
            f"{self._base_url}{_VIDEOS_ENDPOINT}/{task_id}",
            headers=agnes_headers(self._api_key),
        )
        raise_for_status_redacted(resp)
        return resp.json()

    async def _query_video_once(self, client: httpx.AsyncClient, video_id: str) -> dict:
        """One ``video_id`` query; callers choose inner decorators or outer polling to own retries."""
        params = {"video_id": video_id}
        if self.endpoint.v25:
            params["model_name"] = self._model
        resp = await client.get(
            f"{self._host}{_VIDEO_QUERY_ENDPOINT}",
            params=params,
            headers=agnes_headers(self._api_key),
        )
        raise_for_status_redacted(resp)
        return resp.json()

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_poll,
    )
    async def _query_video(
        self,
        client: httpx.AsyncClient,
        video_id: str,
        request: VideoGenerationRequest,
        *,
        record: bool = True,
    ) -> dict:
        """Query video tasks by ``video_id``; 2.5 also sends ``model_name``.

        Primary polling for 2.5; v2.0 uses it only for terminal responses without direct URLs but with video_id.
        Idempotent GET sharing polling retry predicates/evidence boundaries.
        """

        async def fetch() -> dict:
            return await self._query_video_once(client, video_id)

        if not record:
            return await fetch()
        return await recording_poll(fetch, request, stage="result")()

    async def _resolve_video_url(
        self, client: httpx.AsyncClient, final: dict, request: VideoGenerationRequest
    ) -> tuple[str, dict | None]:
        """Two URL sources: use direct completed-response fields when present; otherwise query video_id again.

        Return second-query bodies alongside URLs when queried, otherwise None, so callers can recover duration:
        terminal responses may lack ``seconds`` while second queries provide it.

        When neither source works, errors list field names only, without bodies that may contain signed URLs/sensitive fields,
        matching _safe_body_for_log.
        """
        video_url = _first_url_field(final)
        if video_url is not None:
            return video_url, None

        video_id = final.get("video_id")
        if isinstance(video_id, str) and video_id:
            queried = await self._query_video(client, video_id, request)
            video_url = _first_url_field(queried)
            if video_url is not None:
                return video_url, queried
            raise RuntimeError(f"Agnes 任务完成但 video_id 查询响应缺少成片 URL（字段: {sorted(queried)}）")

        raise RuntimeError(f"Agnes 任务完成但缺少成片 URL 与 video_id（字段: {sorted(final)}）")

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        is_resume: bool,
    ) -> VideoGenerationResult:
        async def poll_fn() -> dict:
            if self.endpoint.v25:
                return await self._query_video_once(client, task_id)
            return await self._poll_once(client, task_id)

        gated_poll = resume_expiry_gate(
            recording_poll(poll_fn, request),
            resume_job_id=task_id if is_resume else None,
            provider=PROVIDER_AGNES,
        )

        final = await poll_with_retry(
            poll_fn=gated_poll,
            is_done=lambda state: state.get("status") in ("completed", "failed"),
            is_failed=_failure_reason,
            max_wait=request.poll_timeout_seconds,
            retry_if=should_retry_poll,
            label="Agnes",
            on_progress=lambda v, elapsed: logger.info(
                "Agnes 视频生成中... status=%s progress=%s elapsed=%ds",
                v.get("status"),
                v.get("progress"),
                int(elapsed),
            ),
        )

        video_url, queried = await self._resolve_video_url(client, final, request)

        await self._download_with_retry(video_url, request.output_path)
        logger.info("Agnes 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_AGNES,
            model=self._model,
            duration_seconds=_extract_duration_seconds(final, queried, request.duration_seconds),
            video_uri=video_url,
            task_id=task_id,
            seed=request.seed,
            generate_audio=self.endpoint.v25,
        )

    @staticmethod
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        """Download completed-video URLs via idempotent GET under shared artifact budgets; never restart generation POSTs."""
        await download_resumable_video(video_url, output_path, label="Agnes")


class Agnes25VideoBackend(AgnesVideoBackend):
    """Agnes 2.5: seconds, Data URI inputs, and completed-video query interfaces."""

    endpoint = AgnesVideoEndpoint(v25=True)
    default_model = DEFAULT_MODEL


AGNES_VIDEO_BACKENDS = {"agnes-video-2": AgnesVideoBackend, "agnes-video-2-5": Agnes25VideoBackend}
