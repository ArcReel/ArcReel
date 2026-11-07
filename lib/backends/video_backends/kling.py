"""KlingVideoBackend: native Kling video backend (direct JWT / relay Bearer, asynchronous polling).

Native video endpoints: submit ``POST /v1/videos/{text2video|image2video}`` for ``data.task_id`` →
poll ``GET /v1/videos/{subpath}/{task_id}`` until ``task_status=succeed`` for
``task_result.videos[0].url`` → download locally. Reuse base.py submit/poll/download helpers;
self-contained async state machine, independent of DashScope async mechanisms.

Two modes, following ``GeminiVideoBackend`` ``backend_type`` precedent:
- ``auth_mode="jwt"`` (built-in providers): access_key + secret_key through ``KlingJWTManager``;
  check expiry before every HTTP call, resign within 60s of expiry because async rendering may outlive tokens.
- ``auth_mode="bearer"`` (custom endpoints): static api_key + base_url, bypassing JWT management.

Model entries record accepted inputs (text, first/last frames, multiple subject references), jointly evaluated with channel declarations (docs/adr/0104).
Selected endpoints declare audio fields and pro-tier last-frame restrictions; request tiers/execution subpaths determine effective values,
jointly evaluated with entries. Multi-image subject subpaths have no audio field; all endpoints declare them silent.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
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
from lib.backends.kling_backend_base import KlingBackendBase
from lib.backends.kling_shared import (
    extract_kling_video_url,
    image_to_base64,
)
from lib.backends.providers import PROVIDER_KLING
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import EntryBoundCapabilities
from lib.backends.video_capabilities import reference_image_limit
from lib.billing.metering import Usage

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kling-v2-5-turbo"

_TEXT2VIDEO = "text2video"
_IMAGE2VIDEO = "image2video"
_MULTI_IMAGE2VIDEO = "multi-image2video"
_RESUMABLE_SUBPATHS = frozenset({_TEXT2VIDEO, _IMAGE2VIDEO, _MULTI_IMAGE2VIDEO})

# Multi-image2video reference capacity (channel facts; orchestration trimming and generation defenses read the same effective capabilities).
# Entries record model maxima (official 3.0 Omni / O1 can reach 7 without reference videos); jointly take the smaller value
# (docs/adr/0104). This subpath conservatively keeps 4, fixed by endpoints.
_R2V_MAX_REFERENCE_IMAGES = 4


@dataclass(frozen=True)
class KlingVideoEndpoint:
    """Kling endpoint request shapes varying with request tier/subpath, verified from official first-party sources.

    Model input acceptance (text, last frames, references) is model fact stored in entries, not this table (docs/adr/0104).
    Retain only request-tier-dependent aspects unresolvable without tier context here.
    """

    # Last frames require pro (official kling-v2-5-turbo/kling-v2-6 first/last frames are pro-only). std image_tail bodies
    # are accepted but last frames do not take effect: video_capabilities_for_tier narrows last_frame in std/4k,
    # and _build_payload rejects image_tail instead of accepting requests callers believe effective but upstream ignores.
    # User-relaxed last frames take precedence.
    last_frame_requires_pro: bool
    # In-video speech capability (official audiovisual-output column): v2-6 / v3 / v3-omni. Also determines whether bodies
    # include sound: official tiers default off; incapable models omit the field instead of suppressing via "off".
    # Public audio form (VideoCapabilities.audio_track) projects through `_audio_track_for`; UI/admission
    # read that projection rather than this bit directly.
    generate_audio: bool
    # Audio only at 1080P (official v2-6: audible videos support 1080P only). Kling has no resolution body field;
    # mode alone determines output tiers, so execution predicates use mode (see `_effective_audio`).
    # Official v3 has no resolution/tier restrictions, hence False.
    audio_requires_1080p: bool


# Unknown models (bearer-pass-through model_name): no tier constraints for last frames, no audio switch, always silent outputs.
_UNRECOGNIZED_VIDEO_CAPS = KlingVideoEndpoint(
    last_frame_requires_pro=False,
    generate_audio=False,
    audio_requires_1080p=False,
)

KLING_PRO_SILENT = KlingVideoEndpoint(True, False, False)
KLING_PRO_AUDIO = KlingVideoEndpoint(True, True, True)
KLING_AUDIO = KlingVideoEndpoint(False, True, False)
KLING_SILENT = _UNRECOGNIZED_VIDEO_CAPS


def _audio_track_for(caps: KlingVideoEndpoint) -> VideoAudioMode:
    """Output audio for text/image-to-video subpaths.

    Both bodies include ``sound`` switches, so audio-capable models are toggleable; models without audio capabilities
    omit the field, defaulting off across Kling tiers and always producing silent outputs. Do not narrow ``audio_requires_1080p`` here:
    it constrains per-request mode, unavailable here. Faithfully declare toggleable under the broadest tier; actual speech
    is decided by ``_effective_audio`` at request time (pricing also uses
    ``effective_generate_audio_for_model``).
    """
    return VideoAudioMode.CONTROLLABLE if caps.generate_audio else VideoAudioMode.ALWAYS_OFF


# mode → resolution (official Kling: std 720P, pro 1080P, 4k 4K).
_MODE_RESOLUTIONS = {"std": "720p", "pro": "1080p", "4k": "4k"}


def _valid_frames(images: list[Path] | None) -> list[Path]:
    """Filter nonempty valid reference paths; empty / None becomes an empty list."""
    if not images:
        return []
    return [Path(img) for img in images if str(img)]


def _has_start_image(request: VideoGenerationRequest) -> bool:
    start_image = request.start_image
    return isinstance(start_image, (str, Path)) and bool(str(start_image))


def _has_end_image(request: VideoGenerationRequest) -> bool:
    end_image = request.end_image
    return isinstance(end_image, (str, Path)) and bool(str(end_image))


def _subpath_for(request: VideoGenerationRequest) -> str:
    """Route: references → multi-image subjects; first frames → image-to-video; neither → text-to-video."""
    if _valid_frames(request.reference_images):
        return _MULTI_IMAGE2VIDEO
    return _IMAGE2VIDEO if _has_start_image(request) else _TEXT2VIDEO


def _encode_job_id(subpath: str, task_id: str, *, generate_audio: bool) -> str:
    """Encode generation subpath + audio flag in persisted job_id (``subpath:task_id:audio``).

    Kling query endpoints differ by generation type (``GET /v1/videos/{text2video|image2video}/{id}``),
    and resumed requests lack ``start_image`` to infer routes. Persist subpaths with task_id,
    otherwise image2video resume would query text2video and fail to find tasks.

    Likewise persist audio flags (0/1) with task_id: resume reuses submission-time decisions rather than
    recomputing from possibly drifted configuration defaults/requests, preventing audio/silent billing drift.
    """
    return f"{subpath}:{task_id}:{1 if generate_audio else 0}"


def _decode_job_id(job_id: str) -> tuple[str, str, bool | None]:
    """Restore ``(子路径, task_id, 有声标志)`` from persisted job_id.

    ``subpath:task_id:audio`` (3 parts, audio 0/1) includes audio; ``subpath:task_id``
    (2 parts) lacks it, returning None for callers to treat as silent. Without known prefixes, fall back to text2video,
    treating the entire string as task_id.
    """
    parts = job_id.split(":")
    if len(parts) == 3 and parts[0] in _RESUMABLE_SUBPATHS and parts[2] in ("0", "1"):
        return parts[0], parts[1], parts[2] == "1"
    prefix, sep, rest = job_id.partition(":")
    if sep and prefix in _RESUMABLE_SUBPATHS:
        return prefix, rest, None
    return _TEXT2VIDEO, job_id, None


class KlingVideoBackend(EntryBoundCapabilities, KlingBackendBase, ProviderJobIdPersistenceMixin):
    """Kling video backend (async polling, JWT / Bearer modes).

    ``KlingBackendBase`` shares authentication/base_url/submit-poll scaffolding; ``provider_job_id`` persistence converges in
    ``ProviderJobIdPersistenceMixin``. This class supplies video differences: subpaths, capability lookups, resume, and downloads.
    """

    _media_label = "视频"
    endpoint_key = "kling-video-pro-silent"
    endpoint = KLING_PRO_SILENT

    def __init__(
        self,
        *,
        auth_mode: str = "jwt",
        access_key: str | None = None,
        secret_key: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
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

    @classmethod
    def video_capabilities_for_model(cls, model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """Kling declarations without request context (channel facts): pure functions without clients/api_key.

        Every model can send text (text2video), first/last frames (image2video ``image_tail``), and multi-image subjects
        (multi-image2video, at most ``_R2V_MAX_REFERENCE_IMAGES`` per call), without audio references. Entries record accepted inputs/reference
        counts, jointly evaluated with declarations (``lib.backends.video_capabilities``, docs/adr/0104).

        Last frames depend on endpoints/request tiers: ``last_frame_requires_pro`` models (kling-v2-5-turbo, kling-v2-6)
        accept only pro. Without caller tier context, conservatively declare disabled; tier-aware callers use
        ``video_capabilities_for_tier``. User-relaxed last frames are sent by ``_build_payload`` according to execution-gate capabilities.
        """
        caps = cls.endpoint
        return VideoCapabilities(
            text_to_video=True,
            first_frame=True,
            last_frame=not caps.last_frame_requires_pro,
            max_reference_images=_R2V_MAX_REFERENCE_IMAGES,
            audio_track=_audio_track_for(caps),
            reference_route_audio_track=VideoAudioMode.ALWAYS_OFF,
        )

    @classmethod
    def effective_generate_audio_for_model(cls, model: str, entry: CatalogEntry | None = None) -> bool:
        """Return effective audio-pricing parameters for default execution tiers without per-request tier context.

        For 1080P-constrained audio models (v2-6), caller quality tiers are unknown, so conservatively return
        False; unconstrained audio models (v3 / v3-omni) produce requested speech, returning True. Execution still uses
        ``_effective_audio`` with actual tiers/subpaths: v3-omni multi-image subjects are silent,
        but this interface lacks reference presence and returns True, overestimating conservatively rather than underreporting cost.
        """
        caps = cls.endpoint
        return caps.generate_audio and not caps.audio_requires_1080p

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model, self._catalog_entry)

    def video_capabilities_for_tier(self, service_tier: str, resolution: str | None = None) -> VideoCapabilities:
        """Narrow last_frame by actual request tiers for context-aware callers.

        `video_capabilities_for_model(model)` is context-free and can only conservatively declare
        `last_frame=False` for `last_frame_requires_pro` models (serving
        `/video-capabilities`, custom provider resolver, and other tier-unaware callers).
        `media_generator` knows `service_tier`/`resolution` before forwarding `end_image`: narrow here;
        permit only pro modes derived from shared `_resolve_mode_from` rules (`resolution="4k"` precedes
        `service_tier`), still rejecting std/4k. This aligns `_build_payload` fail-loud acceptance,
        preventing upstream silent frame loss for pro requests actually accepted by `_build_payload`,
        and false acceptance for 4k+pro (`_resolve_mode` resolves ``"4k"``, never ``"pro"``).
        """
        caps = self.endpoint
        mode = self._resolve_mode_from(resolution, service_tier)
        return replace(
            self.video_capabilities_for_model(self._model, self._catalog_entry),
            last_frame=not caps.last_frame_requires_pro or mode == "pro",
            audio_track=(
                VideoAudioMode.ALWAYS_OFF if caps.audio_requires_1080p and mode != "pro" else _audio_track_for(caps)
            ),
        )

    # ── request building ────────────────────────────────────────────────

    @staticmethod
    def _resolve_mode_from(resolution: str | None, service_tier: str | None) -> str:
        """Quality tier → mode: resolution=4k is independent ``4k`` (v3/v3-omni only), 1080p→``pro``, otherwise service_tier→std/pro.

        Match per_second_tiered pricing derivation (4k before std/pro), keeping request/billing tiers aligned.
        `_resolve_mode` and `video_capabilities_for_tier` share this rule to avoid independently deriving
        conflicting modes for one request, previously causing tier-aware queries to wrongly allow last_frame=True for 4k+pro.
        """
        if (resolution or "").lower() == "4k":
            return "4k"
        if (resolution or "").lower() == "1080p":
            return "pro"  # No resolution field in Kling; 1080p requires pro.
        return "pro" if (service_tier or "").lower() == "pro" else "std"

    def _resolve_mode(self, request: VideoGenerationRequest) -> str:
        return self._resolve_mode_from(request.resolution, request.service_tier)

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

        No resolution field exists in Kling bodies; ``mode`` determines output tiers: std 720p, pro 1080p,
        4k 4k. Audio follows actually sent ``sound`` (``entry`` is the corresponding entry shared with construction); settlement uses
        the equivalent ``result`` flag (restored from job_id on resume, where image fields no longer reveal subpaths).
        """
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=_MODE_RESOLUTIONS[
                KlingVideoBackend._resolve_mode_from(request.resolution, request.service_tier)
            ],
            audio=cls._effective_audio_for(model, request, subpath=_subpath_for(request), entry=entry),
        )

    def _effective_audio(self, request: VideoGenerationRequest, *, subpath: str) -> bool:
        return self._effective_audio_for(self._model, request, subpath=subpath, entry=self._catalog_entry)

    @classmethod
    def _effective_audio_for(
        cls, model: str, request: VideoGenerationRequest, *, subpath: str, entry: CatalogEntry | None = None
    ) -> bool:
        """Actual in-video speech requires user intent + model generate_audio capability + audio-switch-accessible subpath
        + model resolution constraints; user-overridden always-audio tracks bypass resolution constraints.

        Incapable models always return False, avoiding incorrect audio pricing (downstream pricing reads ``result.generate_audio``).

        Require ``subpath`` rather than infer from ``request.reference_images``: rebuilt resume requests omit image fields
        (``media_generator.resume_video_async``); inference would misclassify old-format multi-image job_id as audible.
        Generate receives subpaths from ``_build_payload``, resume decodes job_id. Selected endpoints supply
        resolution constraints/audio declarations.

        Read execution-gate audio capabilities (``request.video_capabilities``, including user relaxations); directly constructed requests
        fall back to ``video_capabilities_for_model``, sharing UI/admission declarations. Native multi-image2video
        schemas lack sound, making this route always silent, matching ``_build_payload`` omission;
        user-overridden audio requirements on this route are rejected before submission by ``_build_payload``.
        """
        if subpath == _MULTI_IMAGE2VIDEO:
            return False
        audio_track = (request.video_capabilities or cls.video_capabilities_for_model(model, entry)).audio_track
        if audio_track is VideoAudioMode.ALWAYS_OFF:
            return False
        if audio_track is VideoAudioMode.ALWAYS_ON:
            # Native models declare toggleable or always-silent only; always-audio means user override: send sound="on" normally for upstream decision,
            # without the 1080P restriction below.
            return True
        if not request.generate_audio:
            return False
        if cls.endpoint.audio_requires_1080p:
            # Official constraints are resolution-based (v2-6 audible output requires 1080P), but bodies lack
            # resolution: mode alone determines tiers; ``request.resolution`` enters payload derivation only to identify 4k.
            # Therefore inspect actual sent mode. Constrained models (v2-6) have std/pro only; pro means
            # 1080P. 4k belongs exclusively to v3 and is illegal for constrained models, never satisfying 1080P:
            # otherwise sound="on" would bill audio for inevitably failed or silent tasks.
            return KlingVideoBackend._resolve_mode_from(request.resolution, request.service_tier) == "pro"
        return True

    def _build_payload(self, request: VideoGenerationRequest) -> tuple[str, dict]:
        """Return (subpath, body).

        Priority: reference_images → multi-image2video (multi-subject R2V);
        start_image → image2video (optional last frames); neither → text2video.
        """
        payload: dict = {
            "model_name": self._model,
            "prompt": request.prompt,
            "mode": self._resolve_mode(request),
            "duration": str(request.duration_seconds),
            "aspect_ratio": request.aspect_ratio,
        }

        # Bodies lack reference-audio fields; even user-relaxed capabilities cannot send them. Reject before billing, never silently discard.
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)

        # Execution-gate capabilities govern sending: user-relaxed dimensions send normally for upstream judgment; direct requests use tier-aware declarations.
        caps = request.video_capabilities or self.video_capabilities_for_tier(
            request.service_tier, resolution=request.resolution
        )
        subpath = _subpath_for(request)
        if subpath == _MULTI_IMAGE2VIDEO:
            reference_images = _valid_frames(request.reference_images)
            # Multi-image subject bodies lack first/last-frame fields: reject mixed requests instead of sending references alone.
            if _has_start_image(request) or _has_end_image(request):
                raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._model)
            # Generation defense (fail-loud): models without multi-image capability cannot upgrade to R2V;
            # likewise reject excessive references, avoiding inevitably failing yet billed requests.
            image_limit = reference_image_limit(caps)
            if not image_limit.accepts_input:
                raise VideoCapabilityError("video_reference_images_unsupported", model=self._model)
            bound = image_limit.clamp_bound()
            if bound is not None and len(reference_images) > bound:
                raise VideoCapabilityError(
                    "video_reference_images_exceeded",
                    model=self._model,
                    count=len(reference_images),
                    limit=bound,
                )
            # Multi-image subject bodies lack sound; user-overridden audio cannot be sent. Reject before billing, never silently produce silent outputs.
            route_audio = caps.audio_track_for_route("r2v")
            if route_audio is VideoAudioMode.ALWAYS_ON or (
                route_audio is VideoAudioMode.CONTROLLABLE and request.generate_audio
            ):
                raise VideoCapabilityError(
                    "video_reference_route_audio_unsupported", provider=self.name, model=self._model
                )
            # Multi-image subjects use image_list=[{"image": <base64>}] (native schema), without a single-first-frame concept.
            payload["image_list"] = [{"image": self._encode_frame(p)} for p in reference_images]
            return _MULTI_IMAGE2VIDEO, payload

        if subpath == _TEXT2VIDEO:
            # No first frames/references means text-to-video intent; reject models without t2v, such as kling-video-o1.
            if not caps.text_to_video:
                raise VideoCapabilityError("video_capability_missing_t2v", provider=self.name, model=self._model)
            # Last frames require image2video image_tail alongside first frames; reject lone last frames instead of discarding through text-to-video.
            if _has_end_image(request):
                raise VideoCapabilityError("video_end_image_requires_start_image", model=self._model)
        else:
            payload["image"] = self._encode_frame(Path(cast(Path, request.start_image)))
            if _has_end_image(request):
                if not caps.last_frame:
                    # For models whose last frames require pro, std/4k image_tail bodies are accepted officially
                    # but ignored. Tier-aware declarations already narrow by tier; report that constraint rather than accept
                    # requests callers believe effective but upstream ignores. Tier restrictions apply only without user-relaxed last frames.
                    if self.endpoint.last_frame_requires_pro and self._resolve_mode(request) != "pro":
                        raise VideoCapabilityError(
                            "video_last_frame_requires_pro", provider=self.name, model=self._model
                        )
                    raise VideoCapabilityError("video_last_frame_unsupported", provider=self.name, model=self._model)
                payload["image_tail"] = self._encode_frame(Path(cast(Path, request.end_image)))

        # Official sound switch ("on"/"off", default off) belongs only to text2video/image2video
        # (native multi-image2video lacks it). Always-silent tracks, including user narrowing, omit it to avoid unknown parameters
        # at unsupported endpoints; user-relaxed toggleable/always-audio tracks send normally.
        if caps.audio_track is not VideoAudioMode.ALWAYS_OFF:
            payload["sound"] = "on" if self._effective_audio(request, subpath=subpath) else "off"
        return subpath, payload

    def _encode_frame(self, path: Path) -> str:
        # Fail loudly for declared unreadable/missing frames instead of silent degradation, which produces wrong yet billed outputs.
        if not path.is_file():
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name)
        try:
            return image_to_base64(path)
        except OSError as exc:
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name) from exc

    @staticmethod
    def _safe_log_view(subpath: str, payload: dict) -> dict:
        """Preredacted scalar views passed directly to logger, avoiding format_kwargs_for_log sinks.

        Never expand frame base64 / prompts: record presence + prompt length only.
        """
        prompt = payload.get("prompt")
        image_list = payload.get("image_list")
        return {
            "endpoint": subpath,
            "model_name": payload.get("model_name"),
            "mode": payload.get("mode"),
            "duration": payload.get("duration"),
            "aspect_ratio": payload.get("aspect_ratio"),
            # Empty string means this tier omits audio switches, distinct from explicit "off".
            "sound": payload.get("sound", ""),
            "has_image": "image" in payload,
            "has_image_tail": "image_tail" in payload,
            "reference_count": len(image_list) if isinstance(image_list, list) else 0,
            "prompt_len": len(prompt) if isinstance(prompt, str) else 0,
        }

    # ── generate / resume ───────────────────────────────────────────────

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # Build payloads in threads: read/encode first/last/reference images without blocking the event loop.
        subpath, payload = await asyncio.to_thread(self._build_payload, request)
        generate_audio = self._effective_audio(request, subpath=subpath)
        logger.info("调用 Kling 视频 API payload=%s", self._safe_log_view(subpath, payload))
        # Creation/polling/downloads share outbound destination gates: user-configured base_url is untrusted.
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            task_id = await self._submit_task(client, f"videos/{subpath}", payload, request)
            logger.info("Kling 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            # Persist subpath:task_id:audio instead of bare task_id so resume restores query endpoints
            # and submission audio decisions (_encode_job_id). Persist actual request hosts too:
            # resume replays them, otherwise changed base_url would query old tasks on new hosts.
            await self._persist_provider_job_id(
                request,
                _encode_job_id(subpath, task_id, generate_audio=generate_audio),
                provider=PROVIDER_KLING,
                endpoint=self._base_url,
            )
            return await self._poll_and_build(client, subpath, task_id, request, generate_audio=generate_audio)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Resume submitted Kling tasks: poll/retrieve URL/download only, never resubmit (ADR 0007).

        Restore query subpaths from persisted job_id, encoded at submission: Kling routes queries by generation type,
        and resume lacks ``start_image`` for inference, so cannot use request fields (_encode_job_id).

        Prefer persisted audio flags too, decided at submission: direct billing separates audio/silent, preventing drifted
        config defaults/requests from changing billing. Two-part ids without flags always mean silent: only implementations
        without audio capabilities created them, so current-capability recomputation would incorrectly bill audio.
        """
        subpath, task_id, persisted_audio = _decode_job_id(job_id)
        generate_audio = persisted_audio if persisted_audio is not None else False
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            return await self._poll_and_build(
                client, subpath, task_id, request, generate_audio=generate_audio, resume_job_id=job_id
            )

    # ── HTTP poll / download ────────────────────────────────────────────

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        subpath: str,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        generate_audio: bool,
        resume_job_id: str | None = None,
    ) -> VideoGenerationResult:
        """Poll to terminal, retrieve and assemble results; nonempty ``resume_job_id`` signals resume and contains persisted job_id."""
        # Resume polling replays submission hosts (None on submit): task IDs are queryable only on creation endpoints;
        # changed base_url would return 404 from new hosts and be misclassified below as expiry.
        base_url = request.submitted_base_url or self._base_url

        gated_poll = resume_expiry_gate(
            recording_poll(lambda: self._poll_query(client, f"videos/{subpath}/{task_id}", base_url=base_url), request),
            resume_job_id=resume_job_id,
            provider=PROVIDER_KLING,
        )

        final = await self._poll_until_terminal(gated_poll, max_wait=request.poll_timeout_seconds)

        download_url = extract_kling_video_url(final)
        await self._download_with_retry(download_url, request.output_path)
        logger.info("Kling 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_KLING,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=download_url,
            task_id=task_id,
            # Actual gate-resolved audio flag; downstream finish_call uses it for audio/silent pricing.
            generate_audio=generate_audio,
        )

    @staticmethod
    async def _download_with_retry(download_url: str, output_path: Path) -> None:
        await download_resumable_video(download_url, output_path, label="Kling")


class KlingProSilentVideoBackend(KlingVideoBackend):
    """``kling-video-pro-silent`` request contract."""

    endpoint_key = "kling-video-pro-silent"
    endpoint = KLING_PRO_SILENT


class KlingProAudioVideoBackend(KlingVideoBackend):
    """``kling-video-pro-audio`` request contract."""

    endpoint_key = "kling-video-pro-audio"
    endpoint = KLING_PRO_AUDIO


class KlingAudioVideoBackend(KlingVideoBackend):
    """``kling-video-audio`` request contract."""

    endpoint_key = "kling-video-audio"
    endpoint = KLING_AUDIO


class KlingSilentVideoBackend(KlingVideoBackend):
    """``kling-video-silent`` request contract."""

    endpoint_key = "kling-video-silent"
    endpoint = KLING_SILENT


KLING_BACKENDS: Mapping[str, type[KlingVideoBackend]] = {
    backend.endpoint_key: backend
    for backend in (
        KlingProSilentVideoBackend,
        KlingProAudioVideoBackend,
        KlingAudioVideoBackend,
        KlingSilentVideoBackend,
    )
}
