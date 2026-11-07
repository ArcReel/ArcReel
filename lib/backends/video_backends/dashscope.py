"""DashScopeVideoBackend: Alibaba Bailian HappyHorse / Wan video generation, asynchronous two-step API.

Native video-generation/video-synthesis endpoints: submit for task_id → poll
GET /tasks/{id} until SUCCEEDED → download video_url. Covers happyhorse-1.0 / happyhorse-1.1
and wan2.7 t2v / i2v / r2v, plus wan3.0's single-model support for all three routes.

Schema verification has two tiers: happyhorse/wan2.7 follow first-party official sources in docs/api-docs/providers/dashscope.md;
wan3.0 has no available official page, deriving request shapes from 2.7. See _WAN3_*
constant comments for provenance and inference boundaries.

Note: t2v/i2v first frames use media[{type:"first_frame"}] (first_frame verified in r2v media
enums); first-party docs do not verify last-frame/continuation fields, so happyhorse/wan2.7 i2v declares
first-frame capability only, without invention. wan3.0 last frames belong to the inference tier above and are exempt.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar, Literal

import httpx

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    ResumeExpiredError,
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
    poll_with_retry,
    recording_poll,
    resume_expiry_gate,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)
from lib.backends.dashscope_shared import (
    dashscope_failure_reason,
    dashscope_headers,
    dashscope_native_base_url,
    extract_billing_duration,
    extract_task_id,
    extract_video_url,
    image_to_data_uri,
    is_dashscope_expired,
    is_dashscope_terminal,
    resolve_dashscope_api_key,
    safe_body_for_log,
)
from lib.backends.data_uri import file_to_data_uri
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.backends.providers import PROVIDER_DASHSCOPE
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import EntryBoundCapabilities, match_by_names
from lib.backends.video_capabilities import reference_image_limit
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    with_retry_async,
)

logger = logging.getLogger(__name__)


def _read_image_or_none(path: Path) -> str | None:
    """Read data URIs; None for missing/nonregular files (including empty-string ".") or I/O failures such as permissions/concurrent deletion."""
    if not path.is_file():
        return None
    try:
        return image_to_data_uri(path)
    except OSError as exc:
        logger.warning("DashScope 图片读取失败: %s (%s)", path, exc)
        return None


# wan2.7 reference_voice accepts wav / mp3 (official Wan 2.7 Reference-to-video, reference_voice section),
# using http / oss / base64 data URI URLs like media.url.
_REFERENCE_AUDIO_MIME_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg"}


def _read_reference_audio_or_none(path: Path) -> str | None:
    """Audio references → base64 data URIs; None for missing files/I/O failures (callers reject formats upfront)."""
    mime = _REFERENCE_AUDIO_MIME_TYPES[path.suffix.lower()]
    if not path.is_file():
        return None
    try:
        return file_to_data_uri(path, mime)
    except OSError as exc:
        logger.warning("DashScope 参考音频读取失败: %s (%s)", path, exc)
        return None


DEFAULT_MODEL = "happyhorse-1.1-i2v"

_VIDEO_ENDPOINT = "/services/aigc/video-generation/video-synthesis"


# All wan2.7 prompts cap at 5000 characters: official tables say excess is automatically truncated, without corresponding error codes.
# Upstream silently truncates while still billing, so gate_video_request rejects excess before billing.
_WAN27_MAX_PROMPT_CHARS = 5000

# wan3.0 single models cover text/image/reference-to-video, limiting prompts to 20000 characters, likewise silently truncating and billing.
# gate_video_request rejects upfront; model entries record reference-image/audio count maxima.
# Source: Wan 3.0 release capability limits. Unlike other models, wan3.0 lacks citable first-party API schemas:
# media types last_frame / reference_audio and parameters["audio"] literals derive from 2.7 shapes.
# Inspect these first for upstream parameter errors.
_WAN3_MAX_PROMPT_CHARS = 20000
_WAN3_MODEL_KEY = "wan3.0-video"

# Wan 3.0 model_id recognition: optional hyphens/underscores, unanchored version number. This backend dispatches request profiles here,
# sharing the sole regex source with lib.custom_provider.duration_presets and endpoints.py.
# All three require identical wan3 matching breadth, avoiding IDs routed here but unrecognized as wan3,
# falling back to generic profiles that lose reference/last-frame/audio fields. Version prefixes and modality tokens
# (WAN_IMAGE_TO_VIDEO_PATTERN below) must accept identical separators, avoiding asymmetric tolerance within one rule family.
# Both identifier boundaries require nonalphanumeric characters to exclude unrelated third-party names like "swan3" / "vendorwan3"
# containing wan3 substrings without belonging to the family.
WAN3_PATTERN = re.compile(r"(?<![a-z0-9])wan[-_]?3(?![a-z0-9])", re.I)

# Wan 2.7 recognition: optional hyphens/underscores, identifier boundaries exclude "swan2" / "wan20".
# Hyphenated "wan-2.7" and dotted "wan2.7" must agree on image/video membership and endpoint routing:
# this is the regex's only verified scope.
#
# Match 2.7 only, excluding other 2.x minors: this backend always requests
# `/services/aigc/video-generation/video-synthesis` (_VIDEO_ENDPOINT), whereas wan2.1 / wan2.2-s2v
# use the old endpoint
# `/services/aigc/image2video/video-synthesis/` with different payloads, such as wan2.6 `size` rather than
# 2.7 `resolution`+`ratio`; expanding this regex would send incompatible protocols here.
# Dotted forms such as "wan2.1-kf2v" are outside this regex, classified by WAN_DOT_FORM_PATTERN. Whether routing should
# narrow them to 2.7 requires vendor API facts/product judgment, not this regex's matching breadth.
WAN2_PATTERN = re.compile(r"(?<![a-z0-9])wan[-_]?2\.7(?![a-z0-9])", re.I)

# Dotted non-2.7 Wan 2.x membership, such as "wan2.1-kf2v": nonalphanumeric identifier boundaries prevent
# unrelated "swan2.7-r2v" / "vendorwan2.7-t2v" names containing "wan2." from matching.
WAN_DOT_FORM_PATTERN = re.compile(r"(?<![a-z0-9])wan2\.\d+(?![a-z0-9])", re.I)

# HappyHorse membership requires nonalphanumeric boundaries, preventing unrelated "myhappyhorse-1.0-r2v" / "happyhorsefoo"
# names from being swallowed by literal substrings.
HAPPYHORSE_PATTERN = re.compile(r"(?<![a-z0-9])happyhorse(?![a-z0-9])", re.I)

# Wan image-to-video continuation syntax: "wan-2.7-image-to-video" / "wan_2.7-image2video" /
# "wan-3-turbo-image-to-video". Recognize only definite "image-(to|2)-video" spelling, not img2vid/i2v
# or other abbreviations. wan2.7 normalization folds to "i2v" for _MODEL_PROFILES; wan3/wan2x_dot use it
# for image/video membership (true image aliases such as wan3.0-video-image lack this syntax, remaining unaffected). Boundaries exclude
# adjacent letters in "wan2.7-fooimage-to-video" / "wan2.7-image-to-videofoo".
WAN_IMAGE_TO_VIDEO_PATTERN = re.compile(r"(?<![a-z0-9])image[-_]?(?:to|2)[-_]?video(?![a-z0-9])", re.I)

# wan2.7-videoedit (instruction-based video editing) is
# a real distinct Wan modality, but this backend implements only t2v/i2v/r2v bodies, without its required
# input-video transport fields. Family matches in this modality must be excluded from native routes/known profiles, or channel
# declarations would lose required request shapes and send unconstructible requests. Identifier boundaries
# exclude unrelated forms such as "videoeditor", with letters immediately after "edit".
WAN_VIDEOEDIT_PATTERN = re.compile(r"(?<![a-z0-9])video[-_]?edit(?![a-z0-9])", re.I)

# wan2.7-s2v (image-to-video speech continuation) / wan2.7-v2v (video-to-video) match families but, like videoedit,
# lack implemented bodies. Unlike has_known_modality, this pattern means known video modality only (used by routing
# to avoid image endpoints), not implemented request construction, which still follows has_known_modality exclusions.
WAN_S2V_V2V_PATTERN = re.compile(r"(?<![a-z0-9])[sv]2v(?![a-z0-9])", re.I)

WanFamily = Literal["happyhorse", "wan2.7", "wan3", "wan2x_dot"]


@dataclass(frozen=True)
class WanClassification:
    """Structured model_id classification in Wan/HappyHorse families.

    Sole classification entry: family membership, separator normalization, identifier boundaries, image-to-video continuation syntax,
    and unimplemented-modality exclusions are implemented once in `classify_wan_model`. Endpoint routing (endpoints.py::infer_endpoint),
    capability profiles (this module's `_profile_for_model`), and duration tiers (duration_presets.py) consume only these
    conclusions, never independently regex-match model_id, preventing drifting family boundaries from producing incompatible combinations
    such as native routing with incompatible capabilities.
    """

    family: WanFamily | None
    is_image_to_video: bool  # Wan image-to-video continuation aliases: video, not image variants.
    is_videoedit: bool  # wan2.7-videoedit: unimplemented request-construction modality.
    profile_key: (
        str | None
    )  # Normalized key directly lookupable in _MODEL_PROFILES; None without identifiable modalities.
    # Whether wan2.7 resolves to implemented t2v/i2v/r2v construction. wan2.7-s2v /
    # wan2.7-v2v / wan2.7-videoedit match families but lack implementations and must remain distinct from implemented modalities:
    # conflation would silently route missing-field requests to incompatible endpoints. wan3/happyhorse/wan2x_dot always
    # True: wan3 covers all modalities in one model; this field does not narrow happyhorse/wan2x_dot modalities (no known exclusions
    # for the former; see profile_key comments for the latter).
    has_known_modality: bool
    # Whether wan2.7 is confirmed video (t2v/i2v/r2v/s2v/v2v/videoedit), even for modalities
    # without implemented bodies (s2v/v2v/videoedit). Unlike has_known_modality, true only for implemented
    # t2v/i2v/r2v, this includes known-but-unimplemented video, distinguishing true image variants
    # such as wan2.7-image without known modality tokens. Unrelated "image" decorations elsewhere in IDs,
    # such as proxy namespaces, cannot override confirmed video meaning. Applies only to wan2.7;
    # otherwise always False: wan3 covers all modalities, happyhorse/wan2x_dot membership is not determined here
    # (see profile_key / has_known_modality).
    is_known_video_modality: bool


def classify_wan_model(model_id: str | None) -> WanClassification:
    """Classify model_id once, sharing conclusions among routing/capability/duration tiers."""
    normalized = (model_id or "").strip().lower()
    if HAPPYHORSE_PATTERN.search(normalized):
        # HappyHorse has no image-to-video continuation or videoedit modality; t2v/i2v/r2v profiles resolve through
        # _profile_for_model final substring fallback, without prenormalization here.
        return WanClassification(
            family="happyhorse",
            is_image_to_video=False,
            is_videoedit=False,
            profile_key=None,
            has_known_modality=True,
            is_known_video_modality=False,
        )
    wan3_match = WAN3_PATTERN.search(normalized)
    wan2_match = None if wan3_match else WAN2_PATTERN.search(normalized)
    wan_dot_match = None if (wan3_match or wan2_match) else WAN_DOT_FORM_PATTERN.search(normalized)
    family_match = wan3_match or wan2_match or wan_dot_match
    if wan3_match:
        family: WanFamily = "wan3"
    elif wan2_match:
        family = "wan2.7"
    elif wan_dot_match:
        family = "wan2x_dot"
    else:
        # Continuation-syntax boundaries and family membership are independent: IDs lacking strict family boundaries
        # such as "wan-2.2-image-to-video" (hyphen between wan/version violates dotted-form boundaries) may still explicitly
        # name video continuation. Missing family membership does not invalidate syntax; preserve it for endpoints.py
        # image-variant exclusion, otherwise broad image checks would misroute to image endpoints. Search scopes still start
        # at literal "wan" substrings (strict boundaries unnecessary; also locate "wan" inside "swan2.7-image"),
        # excluding decorative prefixes; otherwise unrelated proxy prefixes such as "image-to-video-proxy/swan2.7-image"
        # would misclassify true image variants as video continuation. Without literal "wan", downstream never consumes this field
        # (endpoints.py requires `"wan" in lowered` first); whole-string search is sufficient.
        wan_locator = normalized.find("wan")
        fallback_scope = normalized[wan_locator:] if wan_locator != -1 else normalized
        return WanClassification(
            family=None,
            is_image_to_video=bool(WAN_IMAGE_TO_VIDEO_PATTERN.search(fallback_scope)),
            is_videoedit=False,
            profile_key=None,
            has_known_modality=True,
            is_known_video_modality=False,
        )

    # Continuation syntax and videoedit/s2v/v2v are family modality markers: search only modality suffixes after family markers,
    # excluding decorations before them. Otherwise unrelated proxy prefixes such as "image-to-video-proxy/wan2.7-image",
    # "videoedit-proxy/wan2.7-image", "s2v-proxy/wan2.7-image" would masquerade as modalities,
    # hiding true ones (image variants become video continuation, or real t2v/i2v/r2v become unimplemented
    # modalities).
    assert family_match is not None
    family_suffix = normalized[family_match.start() :]
    is_image_to_video = bool(WAN_IMAGE_TO_VIDEO_PATTERN.search(family_suffix))
    is_videoedit = False
    profile_key: str | None = None
    has_known_modality = True
    is_known_video_modality = False
    if family == "wan3":
        profile_key = _WAN3_MODEL_KEY
    elif family == "wan2.7":
        # wan27_suffix comes directly from normalized family_suffix located by family_match, never literal-text search:
        # decorative prefixes may themselves contain literal "wan2.7", such as
        # "vendorwan2.7-videoedit-proxy/wan2.7-r2v". Prefix "wan2.7" violates WAN2_PATTERN boundaries,
        # so is not a family marker, despite being a literal substring. Text searches cannot distinguish noise from actual
        # family markers; only family_match positions reliably anchor them. profile_key restores original decorations
        # without normalization, solely tolerating proxy names (see _find_known_profile_key boundaries).
        wan27_suffix = _normalize_wan27_alias(family_suffix)
        profile_key = normalized[: family_match.start()] + wan27_suffix
        is_videoedit = bool(WAN_VIDEOEDIT_PATTERN.search(wan27_suffix))
        is_s2v_or_v2v = bool(WAN_S2V_V2V_PATTERN.search(wan27_suffix))
        # wan2.7 implements only t2v/i2v/r2v; known-but-unimplemented videoedit/s2v/v2v
        # cannot use native routing. Match known wan2.7 _MODEL_PROFILES keys by identifier boundaries rather than
        # requiring exact wan27_suffix equality: suffixes retain proxy decorations
        # such as "wan2.7-r2v-0715", so equality would reject valid decorated names. Even with known keys,
        # coexisting videoedit/s2v/v2v ("wan2.7-i2v-s2v") remains unimplemented; implemented tokens cannot hide these
        # segments, which must exclude the entire name from implemented scope.
        has_known_modality = (
            not is_videoedit
            and not is_s2v_or_v2v
            and _find_known_profile_key(wan27_suffix, (k for k in _MODEL_PROFILES if k.startswith("wan2.7-")))
            is not None
        )
        # t2v/i2v/r2v (has_known_modality) / videoedit / s2v / v2v are confirmed video modalities despite some
        # unimplemented bodies. Other unconverged names without any known modality tokens conservatively count as images,
        # without assuming image naming forms, matching endpoints.py principles.
        is_known_video_modality = has_known_modality or is_videoedit or is_s2v_or_v2v
    elif family == "wan2x_dot" and is_image_to_video:
        # wan2x_dot has no registered request profiles (profile_key always None, next comment); native routing on
        # image-to-video continuation would leave _match_profile_key unknown, falling back to channel first_frame=True,
        # masking the problem: this backend has no verified first-frame construction for unnarrowed 2.x minors.
        # Exclude IDs without verified capabilities/request schemas from native routing, using the generic video endpoint in 5) below.
        has_known_modality = False
    # wan2x_dot cannot normalize exact t2v/i2v/r2v profiles from unconverged model_id names;
    # leave profile_key empty for _match_profile_key final substring fallback (mostly unknown, using channel declarations).
    # Whether to narrow via known modalities like wan2.7 requires vendor API facts/product judgment, not this field's decision.
    return WanClassification(
        family=family,
        is_image_to_video=is_image_to_video,
        is_videoedit=is_videoedit,
        profile_key=profile_key,
        has_known_modality=has_known_modality,
        is_known_video_modality=is_known_video_modality,
    )


def _normalize_wan27_alias(family_suffix: str) -> str:
    """Normalize WAN2_PATTERN wan2.7 aliases into fixed _MODEL_PROFILES key forms:
    "wan[-_]?2.7[-_]<modality>" → "wan2.7-<modality>", independently of separators among
    wan/version/modality (further fold image-to-video continuation modality into "i2v").

    Two steps are necessary because separators vary independently ("wan_2.7-r2v" only first underscore, "wan-2.7_r2v" only second,
    "wan_2.7_r2v" both). Missing either normalization breaks substring relationships with _MODEL_PROFILES keys,
    silently falling back to channel declarations. Callers must pass substrings starting at the wan2.7 marker itself, excluding
    decorative prefixes, after confirming WAN2_PATTERN.search matches.
    """
    family_suffix = WAN2_PATTERN.sub("wan2.7", family_suffix)
    family_suffix = re.sub(r"wan2\.7_", "wan2.7-", family_suffix)
    return WAN_IMAGE_TO_VIDEO_PATTERN.sub("i2v", family_suffix)


# Bailian channel declarations: one construction can send first/last frames (media first_frame /
# last_frame), references (reference_image), and audio references for every model, without channel-side count limits.
# Entries record model acceptance/counts, jointly evaluated with declarations (lib.backends.video_capabilities, docs/adr/0104).
_CHANNEL_CAPABILITIES = VideoCapabilities(
    text_to_video=True,
    first_frame=True,
    last_frame=True,
    max_reference_images=None,
    reference_audio_mode=ReferenceAudioMode.DIRECT,
    max_reference_audio_count=None,
)


@dataclass(frozen=True)
class DashScopeVideoEndpoint:
    """Different audio/reference-image/voice-attachment contracts under one asynchronous API."""

    audio_switch: bool = False
    reference_required: bool = False
    reference_audio_per_image: bool = False
    wan3_host: bool = False

    @property
    def audio_track(self) -> VideoAudioMode:
        """Audio form depends on whether requests send ``parameters.audio``: without it, upstream defaults to audible outputs.

        No universal audio switch exists across Bailian video models; official sources in ``docs/api-docs/endpoints/dashscope-video.md``.
        Capabilities, requests, and metering share this value, avoiding conflicting interpretations.
        """
        return VideoAudioMode.CONTROLLABLE if self.audio_switch else VideoAudioMode.ALWAYS_ON


# Prompt length still uses model recognition; catalog entries lack this dimension.
_MODEL_PROFILES = {
    "wan2.7-t2v": _WAN27_MAX_PROMPT_CHARS,
    "wan2.7-i2v": _WAN27_MAX_PROMPT_CHARS,
    "wan2.7-r2v": _WAN27_MAX_PROMPT_CHARS,
    _WAN3_MODEL_KEY: _WAN3_MAX_PROMPT_CHARS,
}


def _sent_resolution(request: VideoGenerationRequest) -> str:
    """Bailian resolution parameters use uppercase; send 720P when unspecified."""
    return (request.resolution or "720p").upper()


def _find_known_profile_key(normalized: str, keys: Iterable[str]) -> str | None:
    """Find the first known key in normalized by identifier boundaries; None without matches.

    Both boundaries require nonalphanumeric characters: left boundaries avoid swallowing unrelated names such as "swan2.7-r2v"
    ("s" adjacent to "wan2.7-r2v") or "myhappyhorse-1.0-r2v"; right boundaries avoid truncating unknown variants
    such as "wan2.7-i2vfoo" / "happyhorse-1.0-r2vfoo" into known keys. Proxy decorative prefixes/suffixes
    ("proxy/xxx", "xxx-0715") naturally satisfy both boundaries via nonalphanumeric separators and remain unaffected.
    """
    for key in keys:
        if re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", normalized):
            return key
    return None


def _match_profile_key(model: str) -> str | None:
    """Resolve request-profile keys by name: exact matches first, tolerating proxy prefixes/suffixes; ``None`` if unknown.

    infer_endpoint routes to Bailian async video using the same classify_wan_model conclusions, requiring substring tolerance here too:
    decorated names such as "proxy/happyhorse-1.0-r2v" / "wan2.7-r2v-0715" would otherwise lose r2v request shapes via channel fallback, producing wrong _build_media payloads.
    Bare family names without variants, such as "happyhorse", cannot distinguish t2v/i2v/r2v and intentionally remain unknown.
    """
    normalized = (model or "").strip().lower()
    if not normalized:
        return None
    if normalized in _MODEL_PROFILES:
        return normalized
    classification = classify_wan_model(normalized)
    # has_known_modality=False means infer_endpoint excludes IDs from native routes (unimplemented videoedit/s2v/v2v
    # or unverified wan2x_dot continuation), so profiles must also remain unknown here.
    # Otherwise tolerant substring matches could find coexisting known decorative tokens, such as "i2v" in
    # "wan2.7-i2v-s2v", yielding native-route capabilities for nonnative IDs and contradicting routing.
    if not classification.has_known_modality:
        return None
    if classification.profile_key is not None:
        normalized = classification.profile_key
    # Profile keys happyhorse-{1.0,1.1}-{t2v,i2v,r2v} / wan2.7-{t2v,i2v,r2v} /
    # wan3.0-video are not substrings of each other, so _find_known_profile_key can safely try each boundary match.
    return _find_known_profile_key(normalized, _MODEL_PROFILES)


def _profile_key_for_model(model: str | None, entry: CatalogEntry | None = None) -> str | None:
    """Model request-profile key: resolve names under name_family rules (``entry`` is corresponding entry); ``None`` if all unknown."""
    return match_by_names(model or "", _match_profile_key, entry=entry)


class DashScopeVideoBackend(EntryBoundCapabilities, ProviderJobIdPersistenceMixin):
    """Alibaba Bailian video backend (async video-synthesis); generic ``dashscope-video`` endpoint, without audio switches, always audible."""

    endpoint: ClassVar[DashScopeVideoEndpoint] = DashScopeVideoEndpoint()

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        wan3_base_url: str | None = None,
        http_timeout: float = 60.0,
        transport_layer: TransportLayer | None = None,
    ) -> None:
        self._api_key = resolve_dashscope_api_key(api_key)
        self._base_url = dashscope_native_base_url(base_url)
        # wan3.0-specific MaaS hosts include region/workspace and cannot be inferred/normalized; use the configured
        # full URL unchanged except trailing slashes. Without one, fall back to generic hosts and expose upstream errors faithfully.
        self._wan3_base_url = (wan3_base_url or "").strip().rstrip("/") or None
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._transport_layer = transport_layer
        self._video_capabilities = self.video_capabilities_for_model(self._model)

    def _on_catalog_entry_changed(self) -> None:
        self._video_capabilities = self.video_capabilities_for_model(self._model, self._catalog_entry)

    @property
    def name(self) -> str:
        return PROVIDER_DASHSCOPE

    @property
    def model(self) -> str:
        return self._model

    @classmethod
    def video_capabilities_for_model(cls, model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """Endpoints determine request shapes; only prompt limits use name lookup."""
        return replace(
            _CHANNEL_CAPABILITIES,
            audio_track=cls.endpoint.audio_track,
            reference_audio_per_image=cls.endpoint.reference_audio_per_image,
            max_prompt_chars=_MODEL_PROFILES.get(_profile_key_for_model(model, entry) or ""),
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model, self._catalog_entry)

    @property
    def _request_base_url(self) -> str:
        """Actual request host for this model.

        Submission/polling share it: task IDs are queryable only on creation endpoints; separating hosts would
        submit wan3.0 successfully then poll 404.
        """
        if self._wan3_base_url and self.endpoint.wan3_host:
            return self._wan3_base_url
        return self._base_url

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # Build payloads in threads: first/reference-image/audio reads/encoding must not block the event loop.
        payload = await asyncio.to_thread(self._build_payload, request)
        logger.info(
            "调用 %s 视频 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            task_id = await self._create_task(client, payload, request)
            logger.info("DashScope 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            # Persist actual submission hosts (wan3.0 uses separate MaaS hosts, both user-configurable):
            # resume replays originals, otherwise changed configuration would poll hosts lacking these tasks.
            await self._persist_provider_job_id(
                request, task_id, provider=PROVIDER_DASHSCOPE, endpoint=self._request_base_url
            )
            return await self._poll_and_build(client, task_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Resume submitted DashScope tasks: poll/download only (ADR 0007)."""
        async with artifact_http_client(timeout=self._http_timeout, transport_layer=self._transport_layer) as client:
            return await self._poll_and_build(client, job_id, request, is_resume=True)

    # ── request building ────────────────────────────────────────────────

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

        Resolution uses actually sent tiers (720P when unspecified); settle output seconds from ``usage.duration``. Audio follows endpoint switches;
        absent switches mean audible, matching capabilities. Reference counts use execution-gate effective maxima.
        """
        caps = request.video_capabilities or cls.video_capabilities_for_model(model, entry)
        image_limit = reference_image_limit(caps)
        provided = [r for r in (request.reference_images or []) if r] if image_limit.accepts_input else []
        bound = image_limit.clamp_bound()
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=_sent_resolution(request),
            audio=request.generate_audio if cls.endpoint.audio_switch else True,
            reference_images=len(provided) if bound is None else min(len(provided), bound),
        )

    def _build_payload(self, request: VideoGenerationRequest) -> dict:
        media = self._build_media(request)
        input_block: dict = {"prompt": request.prompt}
        if media:
            input_block["media"] = media

        parameters: dict = {
            "resolution": _sent_resolution(request),
            "duration": request.duration_seconds,
            # HappyHorse defaults to "Happy Horse" watermarks: explicitly disable.
            "watermark": False,
        }
        # Send ratio only without first frames: image-to-video/first-frame reference paths follow first-frame ratios, ignoring ratio upstream
        # (wan2.7 explicitly ignores it with first_frame); HappyHorse image-to-video rejects ratio as invalid outright.
        # Default aspect_ratio is nonempty, so ungated first-frame requests would fail. Only absent first frames require ratio (text/non-first-frame references).
        has_first_frame = any(m.get("type") == "first_frame" for m in media)
        if request.aspect_ratio and not has_first_frame:
            parameters["ratio"] = request.aspect_ratio
        if request.seed is not None:
            parameters["seed"] = request.seed
        # Send audio only on switch-bearing endpoints; others omit unknown parameters, always producing audio.
        if self.endpoint.audio_switch:
            parameters["audio"] = request.generate_audio
        elif not request.generate_audio:
            # Silence cannot be sent; only user-relaxed execution capabilities reach here. Reject before billing instead of producing unwanted audio.
            raise VideoCapabilityError("video_audio_switch_not_supported", provider=self.name, model=self._model)

        return {
            "model": self._model,
            "input": input_block,
            "parameters": parameters,
        }

    def _build_media(self, request: VideoGenerationRequest) -> list[dict]:
        # Execution-gate capabilities determine sending: user-relaxed dimensions go upstream normally; direct requests use model-profile fallback.
        caps = request.video_capabilities or self._video_capabilities
        media: list[dict] = []
        if request.start_image and not caps.first_frame:
            raise VideoCapabilityError("video_first_frame_unsupported", provider=self.name, model=self._model)
        if request.start_image:
            p = Path(request.start_image)
            # Fail loudly for declared missing first frames (directories/nonregular files, including empty-string ".") or read failures;
            # never silently ignore them, leaving users unaware outputs omitted their first frames.
            uri = _read_image_or_none(p)
            if uri is None:
                raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=p.name)
            media.append({"type": "first_frame", "url": uri})
        if request.end_image and not caps.last_frame:
            raise VideoCapabilityError("video_last_frame_unsupported", provider=self.name, model=self._model)
        if request.end_image:
            p = Path(request.end_image)
            uri = _read_image_or_none(p)
            # Likewise fail loudly for unreadable last frames, never silently produce outputs without them.
            if uri is None:
                raise VideoCapabilityError("video_end_image_unreadable", model=self._model, name=p.name)
            media.append({"type": "last_frame", "url": uri})
        reference_items: list[dict] = []
        provided = [r for r in (request.reference_images or []) if r]
        # Reference-to-video endpoints require images: user-relaxed maxima cannot switch endpoints, and narrowed zero maxima
        # cannot allow reference-free requests.
        if not provided and self.endpoint.reference_required:
            raise VideoCapabilityError("video_reference_images_required", model=self._model)
        image_limit = reference_image_limit(caps)
        if not image_limit.accepts_input and provided:
            raise VideoCapabilityError("video_reference_images_unsupported", model=self._model)
        if image_limit.accepts_input:
            # Fail loudly for any missing/unreadable declared reference (is_file failure or read_bytes OSError), listing filenames
            # and aborting. Never silently degrade to reference-free/subset generation, yielding incorrect billed outputs; expose unused inputs.
            data_uris: list[str] = []
            unreadable: list[str] = []
            for r in provided:
                p = Path(r)
                uri = _read_image_or_none(p)
                if uri is None:
                    unreadable.append(p.name)
                else:
                    data_uris.append(uri)
            if unreadable:
                raise VideoCapabilityError(
                    "video_reference_images_unreadable", model=self._model, names=", ".join(unreadable)
                )
            bound = image_limit.clamp_bound()
            if bound is not None and len(data_uris) > bound:
                raise VideoCapabilityError(
                    "video_reference_images_exceeded", model=self._model, limit=bound, count=len(data_uris)
                )
            reference_items = [{"type": "reference_image", "url": uri} for uri in data_uris]
        # Check audio outside reference loops even without attachable references; otherwise nonreference models like wan2.7-i2v
        # would silently discard audio while charging. User-relaxed reference_audio_mode=direct sends normally
        # for upstream decisions; unrelaxed audio profiles fail loudly below. per_image attaches to reference items (wan2.7-r2v), others use
        # separate reference_audio media items (wan3.0).
        standalone_audio_items: list[dict] = []
        if self.endpoint.reference_audio_per_image:
            self._attach_reference_voices(reference_items, request)
        else:
            standalone_audio_items = self._build_reference_audio_items(request)
        media.extend(reference_items)
        media.extend(standalone_audio_items)
        return media

    def _build_reference_audio_items(self, request: VideoGenerationRequest) -> list[dict]:
        """Convert audio references into separate ``reference_audio`` media items (wan3.0).

        Differs from per-clip attachment only in placement: each clip owns a media item without occupying reference slots,
        so slot alignment is irrelevant. Decoding/fail-loud behavior: :meth:`_decode_reference_audio_uris`.
        """
        return [{"type": "reference_audio", "url": uri} for uri in self._decode_reference_audio_uris(request)]

    def _decode_reference_audio_uris(self, request: VideoGenerationRequest) -> list[str]:
        """Read every requested audio reference into data URIs in matching input order.

        Both attachment forms (separate media items and per-clip ``reference_voice``) share decoding here:
        unsupported audio capabilities, unsupported extensions, or any unreadable clip fail loudly.
        Never skip clips: order defines prompt “音频N” references; silently omitting clips breaks character-voice declarations
        while still charging. ``gate_video_request`` validates count/total-duration limits before billing.
        """
        audio_files = list(request.reference_audio_files or [])
        if not audio_files:
            return []
        if (request.video_capabilities or self._video_capabilities).reference_audio_mode == ReferenceAudioMode.NONE:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)
        uris: list[str] = []
        unreadable: list[str] = []
        for audio in audio_files:
            path = Path(audio)
            if path.suffix.lower() not in _REFERENCE_AUDIO_MIME_TYPES:
                raise VideoCapabilityError(
                    "video_reference_audio_format_unsupported",
                    name=path.name,
                    supported=", ".join(sorted(_REFERENCE_AUDIO_MIME_TYPES)),
                )
            uri = _read_reference_audio_or_none(path)
            if uri is None:
                unreadable.append(path.name)
            else:
                uris.append(uri)
        if unreadable:
            raise VideoCapabilityError(
                "video_reference_audio_unreadable", model=self._model, names=", ".join(unreadable)
            )
        return uris

    def _attach_reference_voices(self, reference_items: list[dict], request: VideoGenerationRequest) -> None:
        """Attach each audio reference to reference-item ``reference_voice`` fields in place.

        Prefer ``request.reference_audio_targets`` (which ``reference_items`` index corresponds to clip i):
        audio follows speaker first occurrence, images follow mention first occurrence, independently derived.
        Orchestration (``reference_video`` renderer) already maps voices to images; do not guess by position here.
        When ``reference_audio_targets`` is ``None``, fall back to positional attachment (Nth audio to Nth item).
        Shared ordering is not itself a contract; fallback only supports callers without orchestration-filled mappings,
        such as handwritten tests.

        Hard-fail if audio outnumbers attachable references, never discard extras: discarded character voice declarations
        silently fail until users discover random voices in already-billed videos. Out-of-bounds ``reference_audio_targets``
        likewise hard-fail: orchestration mappings disagree with actual sent reference images,
        requiring visible errors, never silent swallowing.
        """
        audio_files = list(request.reference_audio_files or [])
        if not audio_files:
            return
        if (request.video_capabilities or self._video_capabilities).reference_audio_mode == ReferenceAudioMode.NONE:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)

        targets = request.reference_audio_targets
        if targets is not None:
            # Duplicate target indices mismatch like out-of-bounds ones: multiple clips on one reference item would silently
            # overwrite earlier bindings and lose character voices, requiring hard failure.
            valid = (
                len(targets) == len(audio_files)
                and len(set(targets)) == len(targets)
                and all(0 <= t < len(reference_items) for t in targets)
            )
        else:
            valid = len(audio_files) <= len(reference_items)
        if not valid:
            # Separate this code from gate video_reference_audio_exceeded: gate limits model capabilities,
            # fixable by fewer characters; here limits count actually attachable references,
            # fixable by adding reference images too. Shared codes would suggest incorrect remedies.
            raise VideoCapabilityError(
                "video_reference_audio_slots_insufficient",
                provider=self.name,
                model=self._model,
                slots=len(reference_items),
                count=len(audio_files),
            )
        # Decode after slot validation: insufficient attachable references identifies the request problem earlier
        # than unreadable clips, exposing the real required change at once.
        uris = self._decode_reference_audio_uris(request)
        if targets is not None:
            for idx, uri in zip(targets, uris, strict=True):
                reference_items[idx]["reference_voice"] = uri
        else:
            for item, uri in zip(reference_items, uris, strict=False):
                item["reference_voice"] = uri

    # ── HTTP submit / poll / download ───────────────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(
        self, client: httpx.AsyncClient, payload: dict, request: VideoGenerationRequest | None = None
    ) -> str:
        # Creation is nonidempotent task creation + billing POST: submit_post converts ambiguous transport errors (requests may have reached
        # servers but responses were lost) to terminal AmbiguousSubmitError, avoiding duplicate tasks/billing from automatic retries.
        # >=400 logs body and raises HTTPStatusError via raise_for_status, retaining status_code for choke-point
        # 413 fallback, then should_retry_submit routes by status: 4xx fail fast, 5xx/429 retry.
        resp = await submit_post(
            lambda: client.post(
                f"{self._request_base_url}{_VIDEO_ENDPOINT}",
                json=payload,
                headers=dashscope_headers(self._api_key, async_mode=True),
            ),
            provider=PROVIDER_DASHSCOPE,
            request=request,
        )
        return extract_task_id(resp.json())

    async def _poll_once(self, client: httpx.AsyncClient, task_id: str, base_url: str) -> dict:
        resp = await client.get(
            f"{base_url}/tasks/{task_id}",
            headers=dashscope_headers(self._api_key),
        )
        raise_for_status_redacted(resp)
        return resp.json()

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        is_resume: bool,
    ) -> VideoGenerationResult:
        # Resume polling replays submission hosts: tasks exist only on creation endpoints; changed base_url
        # would poll old tasks on new configuration-derived hosts, returning 404 misclassified below as expiry.
        base_url = request.submitted_base_url or self._request_base_url

        # Resume nonexistent tasks return GET 404, gated into ResumeExpiredError; task_id 24h expiry returns
        # 200 + task_status=UNKNOWN, handled by is_dashscope_expired after terminal polling.
        gated_poll = resume_expiry_gate(
            recording_poll(lambda: self._poll_once(client, task_id, base_url), request),
            resume_job_id=task_id if is_resume else None,
            provider=PROVIDER_DASHSCOPE,
        )

        final = await poll_with_retry(
            poll_fn=gated_poll,
            is_done=is_dashscope_terminal,
            is_failed=dashscope_failure_reason,
            max_wait=request.poll_timeout_seconds,
            retry_if=should_retry_poll,
            label="DashScope",
            on_progress=lambda v, elapsed: logger.info(
                "DashScope 视频生成中... status=%s elapsed=%ds",
                (v.get("output") or {}).get("task_status"),
                int(elapsed),
            ),
        )
        if is_dashscope_expired(final):
            if is_resume:
                raise ResumeExpiredError(
                    job_id=task_id,
                    provider=PROVIDER_DASHSCOPE,
                    message=f"DashScope task expired: {task_id}",
                )
            raise RuntimeError(f"DashScope task expired during generate: {task_id}")

        video_url = extract_video_url(final)
        await self._download_with_retry(video_url, request.output_path)
        logger.info("DashScope 视频下载完成: %s", request.output_path)

        # usage.duration is actual billed duration (wan2.7-r2v includes input-video time); fallback to request duration if absent.
        billing_duration = extract_billing_duration(final)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_DASHSCOPE,
            model=self._model,
            duration_seconds=billing_duration if billing_duration is not None else request.duration_seconds,
            video_uri=video_url,
            task_id=task_id,
            # Settlement uses this flag: endpoints without switches always produce audio; even preupgrade silent submissions report audio on resume.
            generate_audio=request.generate_audio if self.endpoint.audio_switch else True,
        )

    @staticmethod
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        await download_resumable_video(video_url, output_path, label="DashScope")


class DashScopeAlwaysOnVideoBackend(DashScopeVideoBackend):
    """Async video requests without audio switches, always audible (HappyHorse/wan2.7 text/image-to-video).

    Same request contract as generic endpoints; retain separate names for known-model prefills and models with persisted endpoints.
    """

    endpoint = DashScopeVideoEndpoint()


class DashScopeReferenceVideoBackend(DashScopeVideoBackend):
    """Reference-to-video requests require reference images."""

    endpoint = DashScopeVideoEndpoint(reference_required=True)


class DashScopeReferenceVoiceVideoBackend(DashScopeVideoBackend):
    """Reference-to-video voices attach to individual reference images."""

    endpoint = DashScopeVideoEndpoint(reference_required=True, reference_audio_per_image=True)


class DashScopeWan3VideoBackend(DashScopeVideoBackend):
    """Wan3: toggleable audio, separate audio items, and MaaS hosts."""

    endpoint = DashScopeVideoEndpoint(audio_switch=True, wan3_host=True)


DASHSCOPE_VIDEO_BACKENDS = {
    "dashscope-video": DashScopeVideoBackend,
    "dashscope-always-on-video": DashScopeAlwaysOnVideoBackend,
    "dashscope-reference-video": DashScopeReferenceVideoBackend,
    "dashscope-reference-voice-video": DashScopeReferenceVoiceVideoBackend,
    "dashscope-wan3-video": DashScopeWan3VideoBackend,
}
