"""Unified runtime configuration resolver.

Centralize configuration reads and default definitions scattered across files.
Read DB on every call without caching (local SQLite overhead is negligible).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, get_args

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from lib.db.models.custom_provider import CustomProviderModel

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import DurationConstraints
from lib.backends.backend_assembly.specs import (
    builtin_effective_generate_audio_for_model,
    builtin_video_capabilities_for_model,
)
from lib.backends.input_limits import InputLimit
from lib.backends.text_backends.base import TEXT_TASK_TIERS, VISION_REQUIRED_TASKS, TextTaskTier, TextTaskType
from lib.catalog import builtin_models
from lib.catalog import query as catalog
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.service import (
    DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
    DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
    ConfigService,
)
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.episode.episode_target_duration import project_episode_target_duration
from lib.project.project_manager import get_project_manager
from lib.speech.character_voice import CharacterVoiceBinding, character_voice_binding

logger = logging.getLogger(__name__)

# Truthy values for boolean-string parsing.
_TRUTHY = frozenset({"true", "1", "yes"})


@dataclass(frozen=True)
class ProviderModel:
    """Provider-resolution value object: a (canonical provider_id, model_id) pair.

    See GLOSSARY.md ProviderModel. This selects providers/models, **not** backends
    (no clients constructed); naming deliberately avoids ``*Backend`` to distinguish provider identities from backend construction.
    ``provider_id`` is always canonical: resolution assumes canonical inputs (project migrations + write boundaries), without normalization.
    """

    provider_id: str
    model_id: str

    @property
    def pair_key(self) -> str:
        """Compound ``"<provider>/<model>"`` keys, inverse to ``_split_pair``.

        Configuration/payload provider fields and per-model storage keyed by execution models all use this shape;
        all writers construct through this property, defining formats in one place only.
        """
        return f"{self.provider_id}/{self.model_id}"


def _parse_bool(raw: str) -> bool:
    """Parse configuration strings as booleans."""
    return raw.strip().lower() in _TRUTHY


def _parse_int(raw: object, default: int) -> int:
    """Parse configuration values as positive integers; empty / nonnumeric / nonpositive falls back to default (tolerant, never raises)."""
    if isinstance(raw, bool):  # bool subclasses int; exclude explicitly to prevent True→1.
        return default
    if isinstance(raw, int):
        return raw if raw > 0 else default
    if isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
        return value if value > 0 else default
    return default


# Per-provider reference-upload-copy limit override keys (settable through bare API PATCH /providers/{id}/config).
_REFERENCE_TOTAL_MAX_BYTES_KEY = "reference_total_max_bytes"
_REFERENCE_SINGLE_MAX_BYTES_KEY = "reference_single_max_bytes"


def _split_pair(raw: object) -> tuple[str, str] | None:
    """Parse ``"<provider>/<model>"`` → (provider, model); invalid values return None.

    Empty/whitespace provider or model (for example ``"openai/"`` / ``"/m"``) is invalid, returning None
    for callers to supplement bare-provider default models or fall back, preventing empty execution models."""
    if not isinstance(raw, str) or "/" not in raw:
        return None
    provider, model = raw.split("/", 1)
    provider, model = provider.strip(), model.strip()
    if not provider or not model:
        return None
    return provider, model


def _bare_provider(raw: object) -> str | None:
    """Bare project.json provider overrides (bare ``"provider"``, or dirty trailing-slash values missing models like ``"openai/"``) → provider IDs.

    Complete ``"provider/model"``, nonstrings and empty values return None."""
    if not isinstance(raw, str) or _split_pair(raw) is not None:
        return None
    return raw.strip().rstrip("/").strip() or None


def _parse_project_provider(raw: object, media_type: str) -> tuple[str, str] | None:
    """Parse project.json provider fields, supporting bare-provider overrides; catalog reads only, without DB queries.

    - ``"provider/model"`` → (provider, model)
    - Bare ``"provider"`` (registered, with default models for media_type) → (provider, default model)
    - Otherwise → None (delegate to global defaults)

    Write boundaries (``validate_backend_value`` accepts registry keys only) ensure bare providers are canonical IDs. Pin
    providers and supply default models, avoiding silent fallback to **different** global-default providers. Used by synchronous paths without configuration DB sessions;
    layered resolution additionally respects user-disabled defaults (``ConfigResolver._resolve_layered_backend``)."""
    pair = _split_pair(raw)
    if pair is not None:
        return pair
    provider = _bare_provider(raw)
    if provider is not None:
        model = catalog.default_model(provider, media_type)
        if model is not None:
            return provider, model
    return None


def _reject_bare_builtin_without_default(provider: str, media_type: str) -> None:
    """Raise when project bare built-in providers have no usable default for this media type (for example openai has no video models, or defaults are discontinued/disabled).

    Report absent-from-catalog so users reselect, never fall back to different providers for generation/billing.
    """
    if provider in PROVIDER_REGISTRY:
        raise ModelNotInCatalogError(provider_id=provider, model_id="", media_type=media_type)


def _trusted_payload_provider(provider_id: object) -> str | None:
    """Return trustworthy canonical provider_id (known providers), otherwise None.

    Payloads are the resolution chain's sole input bypassing write-boundary validation (in-flight tasks serialized by old enqueue code).
    Guard accordingly: nonstrings / whitespace / unrecognized providers (for example legacy ``seedance``/``vertex``) are never
    trusted. Return None to fall back to migrated project/global values, without legacy→canonical mapping; reject untrusted inputs only."""
    if not isinstance(provider_id, str):
        return None
    provider_id = provider_id.strip()
    if not provider_id:
        return None
    if provider_id in PROVIDER_REGISTRY or is_custom_provider(provider_id):
        return provider_id
    return None


async def _payload_model_or_default(
    session: AsyncSession, raw_model: object, provider_id: str, media_type: str
) -> str | None:
    """Prefer explicit nonempty-string payload models; otherwise supply usable provider defaults (excluding discontinued/user-disabled models).

    Prevent partial payloads (provider only, no model) from passing empty models to execution. Without usable defaults,
    return None for caller project/global fallback."""
    if isinstance(raw_model, str) and raw_model.strip():
        return raw_model.strip()
    return await builtin_models.default_builtin_model(session, provider_id, media_type)


@dataclass(frozen=True)
class _LayeredBackendKeys:
    """Keys for the four-level default + task-type-bucket resolution skeleton, media-independent (see ``docs/adr/0054``).

    Each media/task bucket declares keys consumed by ``ConfigResolver._resolve_layered_backend``
    in fixed order: project bucket > project default > global bucket > global default > automatic inference. None keys mean absent
    layers, skipped directly. New media/buckets require key declarations only, without changing the skeleton.
    """

    media_type: str
    project_bucket_key: str | None = None
    project_default_key: str | None = None
    global_bucket_key: str | None = None
    global_default_key: str | None = None
    # Task-type bucket for these keys, used by automatic inference to filter custom default models. Video and text/audio leave it empty:
    # video custom defaults already allow at most one per media_type at save time; text/audio have no buckets.
    generation_type: str | None = None


# Image bucket (t2i / i2i) keys. Optional overrides; empty buckets fall back to defaults: project defaults use project.json
# default_image_backend (same name/position as text-tier default_text_backend); global defaults use
# default_image_backend setting keys.
_IMAGE_LAYERED_KEYS: dict[str, _LayeredBackendKeys] = {
    generation_type: _LayeredBackendKeys(
        media_type="image",
        project_bucket_key=f"image_provider_{generation_type}",
        project_default_key="default_image_backend",
        global_bucket_key=f"default_image_backend_{generation_type}",
        global_default_key="default_image_backend",
        generation_type=generation_type,
    )
    for generation_type in ("t2i", "i2i")
}


# Video bucket (i2v / r2v) keys. Empty buckets fall back to defaults (docs/adr/0054). Zero migration for defaults: projects
# reuse existing video_backend fields; globals reuse default_video_backend keys.
_VIDEO_LAYERED_KEYS: dict[str, _LayeredBackendKeys] = {
    generation_type: _LayeredBackendKeys(
        media_type="video",
        project_bucket_key=f"video_provider_{generation_type}",
        project_default_key="video_backend",
        global_bucket_key=f"default_video_backend_{generation_type}",
        global_default_key="default_video_backend",
    )
    for generation_type in ("i2v", "r2v")
}


# Unbucketed video keys: same source/layers as ``_VIDEO_LAYERED_KEYS``, excluding buckets (None skipped by skeleton). For callers without
# task-bucket promises (cost estimates, rate-limit-routing fallback, configuration display), without bucket overrides/capability gates.
_VIDEO_DEFAULT_LAYERED_KEYS = _LayeredBackendKeys(
    media_type="video",
    project_default_key="video_backend",
    global_default_key="default_video_backend",
)


# Audio keys. No task buckets: leave bucket layers None, skipped by skeleton. Project defaults use project.json
# audio_backend; globals use default_audio_backend setting keys.
_AUDIO_LAYERED_KEYS = _LayeredBackendKeys(
    media_type="audio",
    project_default_key="audio_backend",
    global_default_key="default_audio_backend",
)

#: Video task buckets: i2v (first-frame-driven image-to-video / grids; also fallback execution for reference-video units without references),
#: r2v (reference-video units with reference images). No t2v bucket (docs/adr/0054).
VideoGenerationType = Literal["i2v", "r2v"]

#: Video task types → buckets. Execution-path mappings are code-fixed (docs/adr/0054): image-to-video /
#: grids (task_type ``video``) → i2v; reference video routes by unit reference-image presence
#: (``lib.script.reference_video.units``). Register representative r2v here only for unreadable script/unit fallback.
#: Unlisted task types have no video bucket; callers handle unbucketed. Defined here rather than lib.backends.generation_type_buckets
#: for layering: queue/worker enqueue/claim paths lie within lib.backends.video_backends' dependency closure and must not
#: indirectly import lib.custom_provider through bucket classification.
VIDEO_BUCKET_BY_TASK_TYPE: dict[str, VideoGenerationType] = {
    "video": "i2v",
    "reference_video": "r2v",
}

#: Generation modes → buckets. Two entries for the same mapping as ``VIDEO_BUCKET_BY_TASK_TYPE``: execution buckets
#: formed task_types; readers (capabilities / duration narrowing, etc.) before task formation have only project
#: generation_mode, choosing the same buckets and therefore the same actual current-configuration execution models.
#: Reference-video imageless-shot fallback (→ i2v) bypasses this table; see ``lib.script.reference_video.units``.
VIDEO_BUCKET_BY_GENERATION_MODE: dict[str, VideoGenerationType] = {
    "storyboard": "i2v",
    "reference_video": "r2v",
}

#: Bucket for unlisted generation_mode (absent project context or dirty data). project.json is plaintext, so generation-mode fields
#: can be manually corrupted; absent project context (provider catalog queries, etc.) also lacks generation modes.
_DEFAULT_VIDEO_BUCKET: VideoGenerationType = "i2v"


def video_bucket_for_generation_mode(generation_mode: str | None) -> VideoGenerationType:
    """Sole reader entry mapping project generation_mode to video task-type buckets.

    project.json is plaintext; nonstring ``generation_mode`` also uses default buckets.
    """
    if not isinstance(generation_mode, str):
        return _DEFAULT_VIDEO_BUCKET
    return VIDEO_BUCKET_BY_GENERATION_MODE.get(generation_mode, _DEFAULT_VIDEO_BUCKET)


def _payload_video_pinned_pair(
    payload: dict, generation_type: VideoGenerationType | None
) -> tuple[VideoGenerationType, tuple[str, str]] | None:
    """Read explicit video execution identities from request payloads (bucket keys ``video_provider_<generation_type>``), returning matched buckets too.

    Values use compound ``ProviderModel.pair_key`` form. Normal queue execution first removes these legacy enqueue payload
    keys, then evaluates current configuration. Submitted-video resume executors construct exactly one bucket key from immutable
    checkpoints to reconstruct original backends. Without declared ``generation_type``, scan both bucket keys
    in fixed order; at most one matches, so bucket order is unambiguous.

    Do not discard untrusted providers here (see ``_trusted_payload_provider``): video identity availability is handled by
    ``_ensure_video_identity_resolvable``. Discontinued providers must raise; fallback would switch execution providers.
    """
    candidates: tuple[VideoGenerationType, ...] = (
        (generation_type,) if generation_type is not None else get_args(VideoGenerationType)
    )
    for candidate in candidates:
        pair = _split_pair(payload.get(f"video_provider_{candidate}"))
        if pair is not None:
            return candidate, pair
    return None


def caps_generation_mode(project: dict | None) -> str | None:
    """Capability-query generation_mode: read project fields directly, None without project context.

    Generation modes are fixed on creation, with entire projects using the same generation path. Capabilities therefore need no episode context:
    bucketing, voice consistency and downstream resolution/reference-image constraints using caps ``generation_mode`` all follow project generation modes.

    Return None rather than defaults because ``generation_mode`` is an external caps field (frontend/Agent):
    absent project context (provider catalog queries, etc.) must not render undeclared values as explicit user mode selections.
    """
    if project is None:
        return None
    mode = project.get("generation_mode")
    return mode if isinstance(mode, str) and mode else None


def project_video_backend_ids(
    project: dict, *, generation_type: VideoGenerationType | None = None
) -> tuple[str, str] | None:
    """Self-declared project.json video identities: use specified bucket keys (default buckets from generation_mode), otherwise project default keys.

    Pure project.json reads without DB, for synchronous paths lacking configuration DB sessions (archive imports query registry tiers by self-declared identities).
    Bucket/default keys coexist in plaintext; never reduce bucket semantics to project defaults, or reference-video projects with
    ``video_provider_r2v`` would use ``video_backend`` tiers. Within-layer values match
    ``_resolve_layered_backend`` project layers, including bare-provider overrides.
    """
    bucket = generation_type or video_bucket_for_generation_mode(project.get("generation_mode"))
    keys = _VIDEO_LAYERED_KEYS[bucket]
    for key in (keys.project_bucket_key, keys.project_default_key):
        if key is None:
            continue
        parsed = _parse_project_provider(project.get(key), "video")
        if parsed is not None:
            return parsed
    return None


def video_capability_satisfied(
    *,
    generation_type: VideoGenerationType,
    first_frame: bool,
    max_reference_images: int | None,
    text_to_video: bool = True,
    has_image: bool = True,
) -> bool:
    """Whether video capability declarations satisfy a bucket; sole bucket-membership predicate.

    Resolution gates (``_ensure_video_bucket_capability``) and bucket dropdowns (``lib.backends.generation_type_buckets``) share
    this function rather than independent boolean formulas: combinations rejected by dropdowns must also fail resolution, and conversely. ``has_image`` distinguishes
    imageless and first-frame requests within i2v. Accept scalars rather than
    ``VideoCapabilities`` to avoid importing arcreel_market_core.video_backend_contract into lib.config (layering contracts), and permit
    effective built-in/custom-provider capabilities (entry/channel evaluation ⊕ overrides) directly. ``max_reference_images`` is the effective
    reference-image limit: ``None`` means uncataloged (accept unlimited references), ``0`` means refused.
    """
    if generation_type == "i2v":
        return first_frame if has_image else text_to_video
    return InputLimit.from_json(max_reference_images).accepts_input


def builtin_video_audio_track(
    provider_id: str, model_id: str, *, generation_type: VideoGenerationType, builtin_endpoint: str | None = None
) -> str | None:
    """Built-in video model output-track shape for this task bucket; None if unresolved (callers do not narrow absent signals).

    Truth comes from backend ``VideoCapabilities``: backends actually construct execution requests, so request-body
    audio-switch presence is literal and only they express branching within one model by execution subpath
    (Kling v3-omni multi-image subject paths omit ``sound``, always outputting silent video). Display, enqueue preflight and
    voice-consistency derivation share this function, never independently interpret declarations.

    Return literals from ``arcreel_market_core.video_backend_contract.VideoAudioMode``. Do not import that enum here: layering places
    lib.config lowest, matching ``derive_voice_consistency`` literal comparison with ``ReferenceAudioMode``
    (``StrEnum`` and literals compare directly with ``==``).

    Three None causes all mean absent signals: unregistered providers (custom providers use
    ``synthesize_video_capabilities`` instead), nonvideo models, or undeclared backend capabilities.
    """
    if catalog.get_model(provider_id, model_id, media_type="video") is None:
        return None
    try:
        caps = builtin_video_capabilities_for_model(provider_id, model_id, endpoint=builtin_endpoint)
    except ValueError:
        return None
    return caps.audio_track_for_route(generation_type).value


# Tiers → setting keys. Globals (system_settings) and projects (project.json) share names/shapes.
_TEXT_TIER_SETTING_KEYS: dict[TextTaskTier, str] = {
    TextTaskTier.SIMPLE: "text_backend_simple",
    TextTaskTier.COMPLEX: "text_backend_complex",
}


# Text-tier keys (docs/adr/0051). Tiers are buckets, sharing project/global names/shapes, so both bucket layers use matching keys;
# both default layers use default_text_backend.
_TEXT_LAYERED_KEYS: dict[TextTaskTier, _LayeredBackendKeys] = {
    tier: _LayeredBackendKeys(
        media_type="text",
        project_bucket_key=tier_key,
        project_default_key="default_text_backend",
        global_bucket_key=tier_key,
        global_default_key="default_text_backend",
    )
    for tier, tier_key in _TEXT_TIER_SETTING_KEYS.items()
}


#: Automatic model-selection preview purposes (``ConfigResolver.auto_resolved_backends``).
AutoResolvePurpose = Literal["t2i", "i2i", "i2v", "r2v", "text"]

#: Purposes → execution-resolution keys. Automatic inference reads only media_type/generation_type;
#: previews sharing keys therefore match execution. Text tiers share both, consolidated into one purpose.
_AUTO_RESOLVE_PURPOSE_KEYS: dict[AutoResolvePurpose, _LayeredBackendKeys] = {
    "t2i": _IMAGE_LAYERED_KEYS["t2i"],
    "i2i": _IMAGE_LAYERED_KEYS["i2i"],
    "i2v": _VIDEO_LAYERED_KEYS["i2v"],
    "r2v": _VIDEO_LAYERED_KEYS["r2v"],
    "text": _TEXT_LAYERED_KEYS[TextTaskTier.COMPLEX],
}


#: Video providers omitting request ``generate_audio`` switches and always billing audio-inclusive tiers.
_VIDEO_AUDIO_ALWAYS_BILLED_PROVIDERS = frozenset({"gemini-aistudio"})


def _video_audio_always_billed(provider_id: str) -> bool:
    """Whether providers always bill audio-inclusive tiers regardless of requests.

    AI Studio Veo requests omit ``generate_audio`` (switch exists only on Vertex pricing pages; AI Studio
    pricing has no audio-off tier). ``GeminiVideoBackend`` settlement forces True for ``backend_type != "vertex"``;
    this is provider-level billing, and registry name ``gemini`` maps to both aistudio / vertex, so classify by
    provider_id rather than static backend interfaces.
    """
    return provider_id in _VIDEO_AUDIO_ALWAYS_BILLED_PROVIDERS


#: Three voice-consistency tiers, matching frontend `VoiceConsistencyTier` one-to-one; update both when adding/removing tiers.
VoiceConsistency = Literal["native", "soft", "none"]


def derive_voice_consistency(
    *,
    reference_audio_mode: str,
    generation_mode: str | None,
    has_audio: bool,
    character_voice_binding: CharacterVoiceBinding | None,
) -> VoiceConsistency:
    """Derive three-level voice consistency (native / soft / none): model capabilities × effective generation_mode × binding mode.

    Sole derivation point repository-wide: project contexts use `_resolve_video_caps_for_model`; catalog queries without
    project context call the same function from `server/routers/providers.py` with ``generation_mode=None`` /
    ``character_voice_binding=None``. Frontend never duplicates formulas.

    Compare ``reference_audio_mode`` literally (``ReferenceAudioMode`` is ``StrEnum``, permitting direct
    ``==``), without importing lib.backends.video_backends into lib.config (layering: config is lowest).

    Native implies output audio: non-reference-video generation_mode or prompt-based soft constraints
    (``character_voice_binding == "prompt"``, default) always downgrade to soft, never none. Downgrades govern the whole chain:
    script-preview reference-audio warnings, prompt ``@音频N`` numbering and execution reference-audio wiring recognize only
    ``native``, so binding modes need not traverse rendering layers. ``has_audio`` asks whether outputs have audio tracks,
    never switch controllability; always-audible models with uncontrollable switches also count. Callers obtain this
    through :func:`builtin_video_audio_track`, never independently interpret declarations.
    """
    if (
        reference_audio_mode == "direct"
        and generation_mode == "reference_video"
        and character_voice_binding == "reference_audio"
    ):
        return "native"
    return "soft" if has_audio else "none"


def _safe_dict(value: object, *, field: str) -> dict:
    """Ensure value supports continued dict resolution; nondicts including None become empty dicts.

    project.json is plaintext; manual edits/external scripts can corrupt nested fields into strings / lists,
    whose direct ``.get()`` raises ``AttributeError``. None explicitly means unconfigured, degrading silently; other nondict
    types log warnings with field names/actual types to locate dirty-data origins.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    logger.warning(
        "project.json field %r has non-dict type %s, falling back to empty dict",
        field,
        type(value).__name__,
    )
    return {}


def _resolution_from_project(project: dict, provider_id: str, model_id: str) -> str | None:
    """project.model_settings (compound ``provider/model`` keys) > legacy video_model_settings > None.

    Defend each nondict intermediate layer with ``_safe_dict`` (see ``_safe_dict`` docstring); leaf values must also
    be trimmed nonempty strings. Downstream ``constrain_durations`` calls ``.strip().lower()``; ints / lists / dicts from manual
    project.json edits or historical dirty data would raise ``AttributeError``. Nonstrings/whitespace always mean unconfigured,
    continuing fallback in line with write-layer tier semantics.
    """
    key = f"{provider_id}/{model_id}"
    model_settings = _safe_dict(project.get("model_settings"), field="model_settings")
    override = _safe_dict(model_settings.get(key), field=f"model_settings.{key}").get("resolution")
    if cleaned := _clean_resolution(override, field=f"model_settings.{key}.resolution"):
        return cleaned
    video_model_settings = _safe_dict(project.get("video_model_settings"), field="video_model_settings")
    legacy = _safe_dict(video_model_settings.get(model_id), field=f"video_model_settings.{model_id}").get("resolution")
    return _clean_resolution(legacy, field=f"video_model_settings.{model_id}.resolution")


def _clean_resolution(value: object, *, field: str) -> str | None:
    """Nonempty strings → trimmed values; everything else (including nonstrings) → None."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        logger.warning("project %s is %s, expected str; treating resolution as unset", field, type(value).__name__)
        return None
    return value.strip() or None


# ---------------------------------------------------------------------------
# Duration-linked constraints.
#
# supported_durations alone does not determine durations: some models (currently all Veo and MiniMax Hailuo) narrow to one value
# at high resolutions or with references, declared in model catalogs (sole truth). This is
# the sole backend consumption entry: narrow candidates before LLM input, dynamic-schema writes or first-item default selection,
# or generated durations will necessarily fail backend execution.
# ---------------------------------------------------------------------------


def constrain_durations(
    provider_id: str | None,
    model_id: str | None,
    durations: list[int],
    *,
    resolution: str | None = None,
    uses_reference_images: bool = False,
) -> list[int]:
    """Narrow candidates by catalog-declared built-in resolution↔duration and reference-image↔duration constraints.

    Uncataloged models retain original candidates. Custom-provider constraints come through :meth:`ConfigResolver.video_duration_constraints`
    and pass to :func:`apply_duration_constraints`.
    """
    return apply_duration_constraints(
        durations,
        builtin_duration_constraints(provider_id, model_id),
        resolution=resolution,
        uses_reference_images=uses_reference_images,
    )


def builtin_duration_constraints(provider_id: str | None, model_id: str | None) -> DurationConstraints | None:
    """Catalog-declared built-in model duration-linked constraints; None for uncataloged models."""
    model_info = catalog.get_model(provider_id, model_id) if provider_id and model_id else None
    if model_info is None:
        return None
    return DurationConstraints(
        by_resolution={res: tuple(allowed) for res, allowed in model_info.duration_resolution_constraints.items()},
        with_reference_images=tuple(model_info.reference_image_durations),
    )


def apply_duration_constraints(
    durations: list[int],
    constraints: DurationConstraints | None,
    *,
    resolution: str | None = None,
    uses_reference_images: bool = False,
) -> list[int]:
    """Narrow candidates by duration-linked constraints.

    Both constraints trigger independently and can apply together, intersecting results. Without constraints return original candidates. Empty intersections return empty sets for consumers to
    handle by stage. Compare resolution keys lowercase.
    """
    if not durations or constraints is None:
        return durations
    allowed = list(durations)
    if uses_reference_images and constraints.with_reference_images:
        allowed = [d for d in allowed if d in constraints.with_reference_images]
    by_resolution = constraints.by_resolution.get(resolution.strip().lower()) if resolution else None
    if by_resolution:
        allowed = [d for d in allowed if d in by_resolution]
    return allowed


#: Duration-exclusion causes: ``resolution`` = unavailable at current resolution, ``reference`` = unavailable with references.
#: Excludes absent-from-model-full-set cases: those are not narrowing; consumers check absence from ``supported_durations``.
DurationExclusionReason = Literal["resolution", "reference"]


#: Endpoint-fixed cause value: sole ``*_endpoint_fixed_reason`` capability-payload value, None when flags are false.
DURATION_ENDPOINT_FIXED_REASON = "endpoint"


def duration_endpoint_fixed_reason(fixed: bool) -> str | None:
    """Endpoint-fixed flag cause: :data:`DURATION_ENDPOINT_FIXED_REASON` when true, otherwise None."""
    return DURATION_ENDPOINT_FIXED_REASON if fixed else None


class NoAvailableProviderError(ValueError):
    """Automatic inference found no available provider."""


class VisionCapabilityError(ValueError):
    """Resolved text models lack image input (vision), preventing vision-required tasks.

    Carry structured fields for callers (for example user-facing routers) to localize as needed; ``str(exc)`` is English technical
    text for logs / non-user-facing paths."""

    def __init__(self, *, task_type: TextTaskType, provider_id: str, model_id: str):
        self.task_type = task_type
        self.provider_id = provider_id
        self.model_id = model_id
        super().__init__(
            f"text model {provider_id}/{model_id} does not support vision, cannot perform task {task_type.value}"
        )


class VideoBucketCapabilityError(ValueError):
    """Video-resolution gate error: resolved models lack required task-bucket capabilities or configured references are unavailable.

    ``code`` is an errors-catalog key and ``params`` its rendering parameters: routers directly localize via
    ``_t(exc.code, **exc.params)``; workers persist structured encoding through ``lib.generation.task_failure.encode_failure``.
    ``str(exc)`` is English technical text for logs / non-user-facing paths."""

    def __init__(
        self,
        *,
        code: str,
        generation_type: VideoGenerationType,
        provider_id: str,
        model_id: str,
        message: str,
    ):
        self.code = code
        self.generation_type = generation_type
        self.provider_id = provider_id
        self.model_id = model_id
        self.params: dict[str, str] = {"provider": provider_id, "model": model_id}
        super().__init__(message)


class VideoSupportedDurationsError(ValueError):
    """Model-row duration declarations missing during capability composition (``missing``).

    ``provider_id`` / ``model_id`` are converged effective identities. Consumers map ``kind`` to route-specific issue codes,
    never parse ``str(exc)``; preserve message text for logs and callers still catching ``ValueError``.
    """

    kind: Literal["missing"]

    def __init__(self, kind: Literal["missing"], *, provider_id: str, model_id: str, message: str):
        self.kind = kind
        self.provider_id = provider_id
        self.model_id = model_id
        super().__init__(message)


class ImageBucketCapabilityError(ValueError):
    """Image-resolution gate error: resolved models lack required task-type-bucket capabilities (t2i / i2i).

    ``code`` shares values with execution-layer ``lib.backends.image_backends.base.ImageCapabilityError``
    (``image_capability_missing_<桶>``): the same issue at resolution/execution, without diverging reader rendering/failure encoding.
    ``params`` supplies rendering parameters; ``str(exc)`` is English technical text for logs / non-user-facing paths.
    """

    def __init__(self, *, generation_type: str, provider_id: str, model_id: str):
        self.code = f"image_capability_missing_{generation_type}"
        self.generation_type = generation_type
        self.provider_id = provider_id
        self.model_id = model_id
        self.params: dict[str, str] = {"provider": provider_id, "model": model_id}
        super().__init__(
            f"image model {provider_id}/{model_id} lacks the capability required by the {generation_type} bucket"
        )


def _video_bucket_capability_missing(
    generation_type: VideoGenerationType, provider_id: str, model_id: str
) -> VideoBucketCapabilityError:
    return VideoBucketCapabilityError(
        code=f"video_capability_missing_{generation_type}",
        generation_type=generation_type,
        provider_id=provider_id,
        model_id=model_id,
        message=f"video model {provider_id}/{model_id} lacks the capability required by the {generation_type} bucket",
    )


def _video_bucket_reference_unavailable(
    generation_type: VideoGenerationType, provider_id: str, model_id: str
) -> VideoBucketCapabilityError:
    return VideoBucketCapabilityError(
        code="video_capability_reference_unavailable",
        generation_type=generation_type,
        provider_id=provider_id,
        model_id=model_id,
        message=f"configured video model {provider_id}/{model_id} is no longer resolvable for the {generation_type} bucket",
    )


def _ensure_text_model_vision_capable(task_type: TextTaskType, provider_id: str, model_id: str) -> None:
    """Validate resolved built-in models accept images; fail directly without silently switching models.

    Use model catalogs only; permit uncataloged models. For custom providers, see ``ConfigResolver._ensure_text_model_vision_capable``."""
    model_info = catalog.get_model(provider_id, model_id)
    if model_info is not None and "vision" not in model_info.capabilities:
        raise VisionCapabilityError(task_type=task_type, provider_id=provider_id, model_id=model_id)


async def _load_project_snapshot(project_name: str | None) -> dict | None:
    """Read project snapshots; project.json file I/O runs in worker threads, never event-loop threads."""

    if not project_name:
        return None
    return await asyncio.to_thread(lambda: get_project_manager().load_project(project_name))


class ConfigResolver:
    """Runtime configuration resolver.

    Thin ConfigService wrapper providing:
    - Sole default-definition point
    - Typed outputs (bool / tuple / dict)
    - Built-in priority resolution (global configuration → project overrides)
    """

    # ── Sole default-definition point ──
    # Matches Seedance / Grok default enabled and storyboard user expectations.
    # server/routers/system_config.py and lib/generation/media_generator.py both reference this constant.
    _DEFAULT_VIDEO_GENERATE_AUDIO = True

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        _bound_session: AsyncSession | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._bound_session = _bound_session

    # ── Session management ──

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[ConfigResolver]:
        """Open shared sessions and return ConfigResolver bound to them."""
        if self._bound_session is not None:
            yield self
        else:
            async with self._session_factory() as sess:
                yield ConfigResolver(self._session_factory, _bound_session=sess)

    @asynccontextmanager
    async def _open_session(self) -> AsyncGenerator[tuple[AsyncSession, ConfigService]]:
        """Obtain (session, ConfigService), preferring bound-session reuse."""
        if self._bound_session is not None:
            yield self._bound_session, ConfigService(self._bound_session)
        else:
            async with self._session_factory() as session:
                yield session, ConfigService(session)

    # ── Public API ──

    async def video_generate_audio(self, project_name: str | None = None) -> bool:
        """Resolve video_generate_audio.

        Priority: project overrides > global configuration > default (True).
        """
        async with self._open_session() as (_session, svc):
            return await self._resolve_video_generate_audio(svc, project_name)

    async def video_generate_audio_for_project(self, project: dict | None) -> bool:
        """Like :meth:`video_generate_audio`, accepting caller-loaded project dicts without rereading disk.

        For callers determining user silence intent **independently** of video capabilities: capability failures (for example
        ``video_capabilities_for_model``) must not lose this value, which never depends on capability interfaces.
        """
        async with self._open_session() as (_session, svc):
            return await self._resolve_video_generate_audio_from_project(svc, project)

    async def default_video_backend(self) -> tuple[str, str]:
        """Return system-default (provider_id, model_id), excluding project overrides."""
        async with self._open_session() as (session, svc):
            return await self._resolve_default_video_backend(svc, session)

    async def video_backend(self, project_name: str | None = None) -> tuple[str, str]:
        """Resolve video (provider_id, model_id) for current projects.

        Priority: project `project.json.video_backend` > system `default_video_backend` >
        auto-resolve (first ready registry-order provider with usable default catalog video models).

        Return literal configuration without converging custom-provider identities, for configuration display and current-selection
        judgments. Use ``resolve_video_backend()`` for actual runtime execution identities.
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_backend(svc, session, project_name)

    async def resolve_image_backend(
        self,
        project: dict | None,
        payload: dict | None,
        *,
        generation_type: Literal["t2i", "i2i"],
    ) -> ProviderModel:
        """Resolve ProviderModel for image tasks.

        Priority: payload > project bucket (``image_provider_<generation_type>``) > project default (``default_image_backend``)
        > global bucket (``default_image_backend_<generation_type>``) > global default (``default_image_backend``) > automatic inference.
        Buckets are optional overrides; absent values (including explicit clearing) fall back to default layers (``docs/adr/0054``).
        ``generation_type`` selects t2i/i2i slots (see ``docs/adr/0001``). Never normalize providers.

        Raises:
            ImageBucketCapabilityError: ValueError subclass; resolved custom models lack required bucket capabilities
                (``_ensure_image_bucket_capability``), without silently switching models.
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_image_provider_model(svc, session, project, payload, generation_type)

    async def resolve_video_backend(
        self,
        project: dict | None,
        payload: dict | None,
        *,
        generation_type: VideoGenerationType | None = None,
        submitted_job: bool = False,
    ) -> ProviderModel:
        """Resolve ProviderModel for video tasks.

        Payloads always have highest priority: prefer materialized execution identities in task-bucket keys ``video_provider_<generation_type>``.
        Then branch by ``generation_type`` (``docs/adr/0054``):

        - Specified ``generation_type`` (``"i2v"`` / ``"r2v"``): four-level skeleton, project bucket (``video_provider_<generation_type>``)
          > project default (``video_backend``) > global bucket (``default_video_backend_<generation_type>``) > global default
          (``default_video_backend``) > automatic inference; empty buckets fall back to defaults. Gate capabilities: missing required bucket
          capabilities or unavailable configured references (deleted models / changed capabilities / deleted providers) raise
          ``VideoBucketCapabilityError``, never silently switch models. Built-in models absent from providers' video catalogs raise
          ``ModelNotInCatalogError``. Payload hits skip capability gates: enqueued tasks execute according to payloads,
          without retroactive validation. Materialized identities still pass identity availability checks; dangling references also fail.
        - None ``generation_type``: same skeleton without buckets, project default (``video_backend``) > global default
          (``default_video_backend``) > automatic inference, without capability gates. Missing/disabled custom-provider models,
          or nonvideo endpoint media_type, converge to provider-default enabled video
          models (**effective runtime identities**); unavailable defaults raise ``ValueError``. For callers without capability promises
          (cost estimates, rate-limit-routing fallback).

        True ``submitted_job`` resumes already submitted provider jobs (polling existing jobs only, never new generation); payloads must pin
        execution identities. Identity availability permits discontinued models; other checks remain. Only resume entries declare it; new submissions always use false.

        Never normalize provider IDs. Use ``video_backend()`` for literal, unconverged configuration results.
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_provider_model(
                svc, session, project, payload, generation_type, submitted_job=submitted_job
            )

    async def resolve_resolution(self, project: dict, provider_id: str, model_id: str) -> str | None:
        """project.model_settings → legacy video_model_settings → custom-provider defaults → None.

        None means omit SDK resolution parameters (see ``docs/adr/0019``). First two layers read project
        dicts without side effects; custom-provider defaults (``CustomProviderModel.resolution``) require DB, making this
        method async and entirely within one session.
        """
        from_project = _resolution_from_project(project, provider_id, model_id)
        if from_project:
            return from_project
        # Only custom providers have DB-side defaults. Built-ins return None directly, avoiding unnecessary sessions for
        # each hot-path generation task (the original separate module also checked is_custom_provider before DB access).
        if not provider_id or not model_id or not is_custom_provider(provider_id):
            return None
        async with self._open_session() as (session, _svc):
            return await self._resolve_custom_resolution_default(session, provider_id, model_id)

    async def _resolve_custom_resolution_default(
        self,
        session: AsyncSession,
        provider_id: str,
        model_id: str,
    ) -> str | None:
        """Custom-provider model default resolution (``CustomProviderModel.resolution``), otherwise None."""
        if not provider_id or not model_id or not is_custom_provider(provider_id):
            return None
        try:
            db_id = parse_provider_id(provider_id)
        except ValueError:
            return None
        repo = CustomProviderRepository(session)
        model = await repo.get_model_by_ids(db_id, model_id)
        return model.resolution if (model and model.resolution) else None

    async def default_audio_backend(self) -> tuple[str, str]:
        """Return system-default audio (provider_id, model_id), excluding project overrides."""
        async with self._open_session() as (session, svc):
            return await self._resolve_default_audio_backend(svc, session)

    async def default_narration_tts(self) -> tuple[ProviderModel, str, float | None]:
        """New TTS project prefill: global-default audio (provider, model), narration voice and speech rate.

        Raise ValueError without configured audio providers. After project creation, never reread these global values (docs/adr/0089).
        """
        async with self._open_session() as (session, svc):
            provider_id, model_id = await self._resolve_default_audio_backend(svc, session)
            return (
                ProviderModel(provider_id, model_id),
                await svc.get_narration_voice(),
                await svc.get_narration_speed(),
            )

    async def resolve_audio_backend(
        self,
        project: dict | None,
        payload: dict | None,
    ) -> ProviderModel:
        """Resolve ProviderModel for speech-synthesis tasks.

        Priority: payload (historical ``audio_provider``) > project (``audio_backend``) > global defaults.
        Speech tasks have no task-type dimension. Never normalize providers.
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_audio_provider_model(svc, session, project, payload)

    async def video_capabilities_for_project(
        self,
        project: dict,
        *,
        generation_type: VideoGenerationType | None = None,
    ) -> dict:
        """Resolve combined video-model capabilities + user preferences for caller-loaded current projects.

        Bucket models by project ``generation_mode`` (image-to-video / grids → i2v, reference video → r2v), then use the same resolution
        as execution, always describing the actual model current configuration will execute (``docs/adr/0054``).
        Generation modes are fixed on creation, used throughout projects; resolution needs no episode context.

        Without ``generation_type``, bucket by project generation_mode; otherwise resolve specified buckets, for reference-video
        readers routing by unit (imageless units use i2v tiers / pricing).

        ``supported_durations`` is the full model-declared set; video request facts supply tiers narrowed by request resolution/references
        (``lib.generation.video_request_facts``), never assembled during capability composition.

        Returns:
            {
              "provider_id": str,
              "model": str,
              "supported_durations": list[int],    # From model (sole truth); empty sets valid on ComfyUI endpoints
              "max_duration": int,                 # Derived max(supported_durations); 0 for empty sets
              "max_reference_images": int | None,  # Effective (entry/channel evaluation ⊕ overrides); None uncataloged, 0 refused
              "first_frame": bool,                 # Effective (both-side evaluation ⊕ overrides), shared with execution
              "last_frame": bool,                  # As above
              "generate_audio": bool,              # Pricing parameter after backend-default execution tiers apply
              "requested_generate_audio": bool,    # User silence intent in project.json/global settings, not pricing semantics
              "max_reference_audio_count": int | None,  # Effective per-request reference-audio segment limit; None uncataloged
              "reference_audio_per_image": bool,   # Whether audio segments must attach individually to specific reference assets (backend declaration)
              "source": "registry" | "custom",
              "default_duration": int | None,      # User preference in project.json
              "episode_target_duration": int | None,  # User per-episode target seconds, a soft preference, never model capability
              "content_mode": str | None,
              "generation_mode": str | None,       # Project generation mode (None without project context)
              "voice_consistency": "native" | "soft" | "none",  # Model capabilities × generation_mode × binding mode
            }

        Raises:
            ValueError: Failed video_backend resolution / missing models / empty supported_durations
                (except ComfyUI endpoints, where empty means durations are not driven by ArcReel and is valid).
            VideoBucketCapabilityError: ValueError subclass; missing required bucket capabilities or unavailable
                configured references.
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_capabilities_from_project(
                svc, session, project, generation_type=generation_type
            )

    async def video_duration_constraints(self, provider_id: str, model_id: str) -> DurationConstraints | None:
        """Video-model duration-linked constraints (model facts); None without constraints.

        Built-ins use catalogs; custom-provider models use capability overrides > corresponding entries, without constraints if no entry matches. ComfyUI endpoint
        durations derive only from node bindings, without linked constraints. Unresolved model rows/endpoints return None: capability resolution reports invalid references,
        never duplicate errors here.
        """
        if not is_custom_provider(provider_id):
            return builtin_duration_constraints(provider_id, model_id)
        from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
        from lib.custom_provider.model_facts import video_model_facts
        from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository

        async with self._open_session() as (session, _svc):
            try:
                model = await CustomProviderRepository(session).get_model_by_ids(
                    parse_provider_id(provider_id), model_id
                )
                if model is None:
                    return None
                endpoint_spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
            except ValueError:
                return None
            if endpoint_spec.endpoint_durations is not None:
                return None
            return video_model_facts(
                model_id, model.capability_overrides, entry_binding=model.entry_binding
            ).duration_constraints

    async def video_audio_track(
        self,
        provider_id: str,
        model_id: str,
        *,
        generation_type: VideoGenerationType,
        builtin_endpoint: str | None = None,
    ) -> str | None:
        """Video-model output-track shape for this task bucket (``VideoAudioMode`` literals); None without signals.

        For built-ins, see :func:`builtin_video_audio_track`. Custom-provider models use only user audio-track capability overrides, matching
        :meth:`video_capabilities_for_model` voice-consistency derivation. Missing overrides/unresolved rows/endpoints return None;
        callers never narrow absent signals. Capability resolution reports invalid references, without duplicating errors here.
        """
        if not is_custom_provider(provider_id):
            return builtin_video_audio_track(
                provider_id, model_id, generation_type=generation_type, builtin_endpoint=builtin_endpoint
            )
        from lib.custom_provider.capabilities import (
            overridden_audio_track,
            synthesize_video_capabilities_with_overrides,
        )
        from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
        from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository

        async with self._open_session() as (session, _svc):
            try:
                model = await CustomProviderRepository(session).get_model_by_ids(
                    parse_provider_id(provider_id), model_id
                )
                if model is None:
                    return None
                endpoint_spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
                caps, applied_overrides = synthesize_video_capabilities_with_overrides(
                    endpoint=model.endpoint,
                    model_id=model_id,
                    overrides=model.capability_overrides,
                    endpoint_spec=endpoint_spec,
                    entry_binding=model.entry_binding,
                )
            except ValueError:
                return None
        return overridden_audio_track(caps, applied_overrides, generation_type)

    async def video_capabilities_for_model(
        self,
        provider_id: str,
        model_id: str,
        project: dict | None = None,
        *,
        generation_type: VideoGenerationType | None = None,
        builtin_endpoint: str | None = None,
    ) -> dict:
        """Read specified provider/model video capabilities without resolving providers again.

        For execution callers already resolving actual ProviderModel via `resolve_video_backend(project, payload)`
        (including historical task payload overrides). This variant ensures duration
        guards use supported_durations for the actual called model, avoiding validation of payload-resolved models
        against project-default model capabilities.

        Input identities still converge again (same rules as ``resolve_video_backend``), so literal configuration inputs also
        obtain effective-identity capabilities; unavailable custom-provider defaults raise ``ValueError``.

        Two-dimensional values such as voice consistency derive by ``project`` generation modes; explicit ``generation_type`` derives
        per-path capabilities by task bucket (track shapes branch by execution subpaths; see :func:`builtin_video_audio_track`).
        Execution knows actual buckets; pass them for results matching real request shapes.
        ``builtin_endpoint`` is the built-in endpoint selected during backend construction; otherwise read current model endpoint associations.
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_caps_for_model(
                svc,
                session,
                provider_id,
                model_id,
                project,
                generation_type=generation_type,
                builtin_endpoint=builtin_endpoint,
            )

    async def video_pricing_generate_audio(
        self,
        provider_id: str,
        model_id: str,
        project: dict | None = None,
    ) -> bool:
        """Effective pricing ``generate_audio``: read capabilities, degrade on unresolved values, never raise.

        Capability resolution raises for discontinued catalog model IDs, but estimates must continue (pricing marks them unpriced). Two fallback layers:
        always-audio-billed providers use provider-level True; other providers lack default-execution-tier information,
        so return request values (also what backends submit/settle). Universally returning False underestimates these historical
        models at silent tiers.
        """
        try:
            caps = await self.video_capabilities_for_model(provider_id, model_id, project)
        except Exception as exc:
            logger.info(
                "视频能力解析失败（%s/%s），计价 generate_audio 降级到 provider 级规则与请求值：%s",
                provider_id,
                model_id,
                exc,
            )
            if _video_audio_always_billed(provider_id):
                return True
            async with self._open_session() as (_session, svc):
                return await self._resolve_video_generate_audio_from_project(svc, project)
        return bool(caps["generate_audio"])

    async def default_image_backend_t2i(self) -> tuple[str, str]:
        """Return (provider_id, model_id), T2I defaults."""
        async with self._open_session() as (session, svc):
            return await self._resolve_default_image_backend(svc, session, "t2i")

    async def default_image_backend_i2i(self) -> tuple[str, str]:
        """Return (provider_id, model_id), I2I defaults."""
        async with self._open_session() as (session, svc):
            return await self._resolve_default_image_backend(svc, session, "i2i")

    async def default_image_backend(self) -> tuple[str, str]:
        """Compatibility shim for legacy callers; returns T2I variants."""
        return await self.default_image_backend_t2i()

    async def auto_resolved_backends(self) -> dict[AutoResolvePurpose, tuple[str, str] | None]:
        """Automatically inferred (provider_id, model_id) when all project/global layers for each purpose are empty, None if unavailable.

        Read-only previews for settings UI to show actual automatically chosen models and query capabilities.
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_auto_backends(svc, session)

    async def provider_config(self, provider_id: str) -> dict[str, str]:
        """Read one provider configuration."""
        async with self._open_session() as (session, svc):
            return await self._resolve_provider_config(svc, session, provider_id)

    async def provider_config_with_credential(self, provider_id: str) -> tuple[dict[str, str], int | None]:
        """Read one provider configuration with overlaid effective credential-record IDs; None IDs without effective credentials.

        Obtain both in one read: material-library bindings verify credential records against actual contents; separate reads mismatch if credentials switch between them.
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_provider_config_and_credential(svc, session, provider_id)

    async def all_provider_configs(self) -> dict[str, dict[str, str]]:
        """Read all provider configurations in batches."""
        async with self._open_session() as (session, svc):
            return await self._resolve_all_provider_configs(svc, session)

    async def reference_payload_limits(self, provider_id: str | None) -> tuple[int, int]:
        """Resolve (total_max_bytes, single_max_bytes) for reference upload copies.

        Priority: per-provider overrides > conservative shared service defaults. None provider_id (zero configuration /
        generic limits) returns default tuples directly without DB access. Return plain int tuples without importing reference_compression,
        avoiding indirect PIL imports into widely depended-on resolvers.
        """
        if provider_id is None:
            return DEFAULT_REFERENCE_TOTAL_MAX_BYTES, DEFAULT_REFERENCE_SINGLE_MAX_BYTES
        async with self._open_session() as (session, svc):
            return await self._resolve_reference_payload_limits(svc, session, provider_id)

    # ── Internal resolution methods (independently testable, accepting existing svc) ──

    async def _resolve_video_generate_audio(
        self,
        svc: ConfigService,
        project_name: str | None,
    ) -> bool:
        project = await _load_project_snapshot(project_name)
        return await self._resolve_video_generate_audio_from_project(svc, project)

    async def _resolve_video_generate_audio_from_project(
        self,
        svc: ConfigService,
        project: dict | None,
    ) -> bool:
        raw = await svc.get_setting("video_generate_audio", "")
        value = _parse_bool(raw) if raw else self._DEFAULT_VIDEO_GENERATE_AUDIO

        if project is not None:
            override = project.get("video_generate_audio")
            if override is not None:
                value = _parse_bool(override) if isinstance(override, str) else bool(override)

        return value

    async def _resolve_default_video_backend(self, svc: ConfigService, session: AsyncSession) -> tuple[str, str]:
        """Resolve global video defaults only: global default keys > automatic inference.

        Use four-level skeleton without projects (project=None skips project layers), without bucket keys (``_VIDEO_DEFAULT_LAYERED_KEYS``).
        """
        return await self._resolve_layered_backend(svc, session, None, _VIDEO_DEFAULT_LAYERED_KEYS)

    async def _resolve_video_backend(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project_name: str | None,
    ) -> tuple[str, str]:
        """Three-level current-project video backend resolution: project defaults > global defaults > automatic inference.

        Use unbucketed four-level skeleton keys (``_VIDEO_DEFAULT_LAYERED_KEYS``), skipping absent bucket layers.
        """
        project = await _load_project_snapshot(project_name)
        return await self._resolve_layered_backend(svc, session, project, _VIDEO_DEFAULT_LAYERED_KEYS)

    async def _resolve_layered_backend(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project: dict | None,
        keys: _LayeredBackendKeys,
    ) -> tuple[str, str]:
        """Four-level default + task-type-bucket skeleton: project bucket > project default > global bucket > global default > automatic inference.

        Media-independent, with layer keys declared by ``_LayeredBackendKeys`` (see ``docs/adr/0054``). Project
        fields permit bare-provider overrides (supply usable provider defaults, excluding discontinued/user-disabled models; raise if unavailable). Global layers require complete ``provider/model``
        shapes. Payloads and runtime identity convergence (for example effective custom-provider video identities) lie outside
        this skeleton, handled externally by media callers.
        """
        if project:
            for project_key in (keys.project_bucket_key, keys.project_default_key):
                if project_key is None:
                    continue
                raw = project.get(project_key)
                provider = _bare_provider(raw)
                if provider is None:
                    pair = _split_pair(raw)
                    if pair is not None:
                        return pair
                    continue
                default = await builtin_models.default_builtin_model(session, provider, keys.media_type)
                if default is not None:
                    return provider, default
                _reject_bare_builtin_without_default(provider, keys.media_type)
        settings = await svc.get_all_settings()
        if keys.global_bucket_key is not None:
            raw = settings.get(keys.global_bucket_key, "")
            if "/" in raw:
                provider_id, model_id = raw.split("/", 1)
                return provider_id, model_id
        if keys.global_default_key is not None:
            raw = settings.get(keys.global_default_key, "")
            if "/" in raw:
                provider_id, model_id = raw.split("/", 1)
                return provider_id, model_id
        return await self._auto_resolve_backend(svc, session, keys.media_type, keys.generation_type)

    async def _resolve_image_provider_model(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project: dict | None,
        payload: dict | None,
        generation_type: Literal["t2i", "i2i"],
    ) -> ProviderModel:
        """Resolve image ProviderModel payload-first, otherwise use four-level skeleton.

        Payloads preserve canonical ``payload>project>global`` resolution, accepting ``image_provider`` /
        ``image_model`` keys for tasks serialized in that format. Image tasks do not pin execution identities (short lifetimes,
        small queue-time configuration drift windows), so payloads have no task-bucket keys. Payload providers must be known
        (see
        ``_trusted_payload_provider``); otherwise distrust and fall back to skeleton (``_resolve_layered_backend``,
        keys in ``_IMAGE_LAYERED_KEYS``).

        Both layers' resolved identities pass capability gates (``_ensure_image_bucket_capability``): image buckets are determined only during execution
        (``docs/adr/0001``), and payloads lack materialized bucket keys eligible for exemption.
        """
        selected: ProviderModel | None = None
        if payload:
            provider_id = _trusted_payload_provider(payload.get("image_provider"))
            if provider_id is not None:
                model = await _payload_model_or_default(session, payload.get("image_model"), provider_id, "image")
                if model is not None:
                    selected = ProviderModel(provider_id, model)
        if selected is None:
            provider_id, model_id = await self._resolve_layered_backend(
                svc, session, project, _IMAGE_LAYERED_KEYS[generation_type]
            )
            selected = ProviderModel(provider_id, model_id)
        await self._ensure_image_bucket_capability(session, selected, generation_type)
        return selected

    async def _ensure_image_bucket_capability(
        self,
        session: AsyncSession,
        selected: ProviderModel,
        generation_type: Literal["t2i", "i2i"],
    ) -> None:
        """Capability gate: fail directly if resolved custom models lack bucket capabilities, never silently switch models.

        Share bucket-dropdown rules (``lib.backends.generation_type_buckets``) through
        ``lib.custom_provider.default_models.lacks_bucket``. Two differences from
        ``_ensure_video_bucket_capability``:

        - Custom providers only. Execution-layer ``MediaGenerator`` gates built-in image bucket capabilities with the same effective
          capabilities (``lib.backends.image_capabilities``), using ``image_capability_missing_*`` codes shared with this gate.
        - Invalid references (deleted/disabled models / endpoints changed to other media types) do not fail here: image design falls back to
          bucket-default models (``lib.custom_provider.loader``), also bucket-filtered.
        """
        if not is_custom_provider(selected.provider_id):
            return

        # Lazy import: layering (pyproject.toml [tool.importlinter]) places lib.config below assembly;
        # this symbol's assembly layer depends on lib.config, so module-level imports would cycle.
        from lib.custom_provider.default_models import lacks_bucket

        try:
            db_pid = parse_provider_id(selected.provider_id)
        except ValueError:
            return
        model = await CustomProviderRepository(session).get_model_by_ids(db_pid, selected.model_id)
        if model is None or not model.is_enabled:
            return
        if await lacks_bucket(session, model, generation_type):
            raise ImageBucketCapabilityError(
                generation_type=generation_type,
                provider_id=selected.provider_id,
                model_id=selected.model_id,
            )

    async def _resolve_video_provider_model(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project: dict | None,
        payload: dict | None,
        generation_type: VideoGenerationType | None = None,
        *,
        submitted_job: bool = False,
    ) -> ProviderModel:
        """Resolve video ProviderModel payload-first; otherwise use bucketed/unbucketed skeletons by ``generation_type``.

        Payloads recognize only materialized task-bucket keys (compound ``video_provider_<generation_type>`` values; see
        ``_payload_video_pinned_pair``). Return unchanged, without capability gates, checking identity availability only
        (``_ensure_video_identity_resolvable``); dangling references fail. Pinned identities never discard untrusted providers:
        fallback from discontinued providers would change execution providers. See ``resolve_video_backend`` for layer semantics.

        ``submitted_job`` applies only to pinned identities: resume identities originate from submission-frozen checkpoints. Missing pins mean callers are not resuming;
        raise directly, never apply exemptions to identities resolved from current configuration.
        """
        pinned = _payload_video_pinned_pair(payload, generation_type) if payload else None
        if pinned is not None:
            pinned_generation_type, pair = pinned
            selected = ProviderModel(*pair)
            await self._ensure_video_identity_resolvable(
                session, selected, pinned_generation_type, submitted_job=submitted_job
            )
            return selected
        if submitted_job:
            raise ValueError(
                "续跑已提交的视频任务须在 payload 里锁定提交时的执行身份（video_provider_<generation_type>）"
            )
        if generation_type is None:
            provider_id, model_id = await self._resolve_layered_backend(
                svc, session, project, _VIDEO_DEFAULT_LAYERED_KEYS
            )
            return await self._resolve_effective_video_provider_model(session, ProviderModel(provider_id, model_id))
        provider_id, model_id = await self._resolve_layered_backend(
            svc, session, project, _VIDEO_LAYERED_KEYS[generation_type]
        )
        selected = ProviderModel(provider_id, model_id)
        await self._ensure_video_bucket_capability(session, selected, generation_type)
        return selected

    async def _ensure_video_identity_resolvable(
        self,
        session: AsyncSession,
        selected: ProviderModel,
        generation_type: VideoGenerationType,
        *,
        submitted_job: bool = False,
    ) -> CustomProviderModel | None:
        """Validate video identities still resolve; dangling references fail.

        Check identity existence, not capability declarations: built-ins use model catalogs. Discontinued models raise ``ModelDiscontinuedError`` (dates/successors);
        models absent from provider video catalogs (renamed or nonvideo) raise ``ModelNotInCatalogError``, sharing assembly/image
        reselect-model semantics (``catalog.require_callable``). Custom models use DB (deleted providers, deleted/disabled models, endpoints changed media types);
        dangling references raise ``VideoBucketCapabilityError``, discontinued corresponding catalog entries raise ``ModelDiscontinuedError`` too. Custom hits return rows for inline capability composition without duplicate
        DB queries; built-ins return None.

        Not all backend caps functions validate model existence or media_type (some providers return static capabilities for arbitrary model IDs,
        so image models from the same provider can receive first_frame=True), requiring separate identity checks.

        True ``submitted_job`` (resuming submitted provider jobs) permits discontinued models; other checks remain.
        """
        provider_id, model_id = selected.provider_id, selected.model_id
        if not is_custom_provider(provider_id):
            if submitted_job:
                catalog.require_listed(provider_id, model_id, "video")
            else:
                catalog.require_callable(provider_id, model_id, "video")
            return None

        # Lazy import: layering (pyproject.toml [tool.importlinter]) places lib.config below assembly;
        # this symbol's assembly layer depends on lib.config, so module-level imports would cycle.
        from lib.custom_provider.default_models import requested_model

        try:
            db_pid = parse_provider_id(provider_id)
        except ValueError as exc:
            raise _video_bucket_reference_unavailable(generation_type, provider_id, model_id) from exc
        model = await requested_model(
            session,
            provider_id=provider_id,
            db_id=db_pid,
            model_id=model_id,
            media_type="video",
            submitted_job=submitted_job,
        )
        if model is None:
            raise _video_bucket_reference_unavailable(generation_type, provider_id, model_id)
        return model

    async def _ensure_video_bucket_capability(
        self,
        session: AsyncSession,
        selected: ProviderModel,
        generation_type: VideoGenerationType,
    ) -> None:
        """Capability gate: validate resolved models satisfy bucket requirements; fail directly without silently switching models.

        ``video_capability_satisfied`` shares rules with bucket dropdowns (``lib.backends.generation_type_buckets``):
        both dimensions use effective channel/model-entry capabilities (docs/adr/0104), matching execution gates. Identities first pass
        ``_ensure_video_identity_resolvable``. Dangling references (deleted models /
        subsequently changed capabilities / deleted providers / endpoint changes) fail uniformly here; writes do not block or cascade cleanup
        (``docs/adr/0054``).
        """
        provider_id, model_id = selected.provider_id, selected.model_id
        model = await self._ensure_video_identity_resolvable(session, selected, generation_type)
        if model is not None:
            # Lazy import: layering (pyproject.toml [tool.importlinter]) places lib.config below assembly;
            # this symbol's assembly layer depends on lib.config, so module-level imports would cycle.
            from lib.custom_provider.capabilities import synthesize_video_capabilities

            try:
                from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
                from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository

                endpoint_spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
                caps = synthesize_video_capabilities(
                    endpoint=model.endpoint,
                    model_id=model_id,
                    overrides=model.capability_overrides,
                    endpoint_spec=endpoint_spec,
                    entry_binding=model.entry_binding,
                )
            except ValueError as exc:
                raise _video_bucket_reference_unavailable(generation_type, provider_id, model_id) from exc
        else:
            try:
                caps = builtin_video_capabilities_for_model(provider_id, model_id)
            except ValueError as exc:
                raise _video_bucket_reference_unavailable(generation_type, provider_id, model_id) from exc
        satisfied = video_capability_satisfied(
            generation_type=generation_type,
            first_frame=caps.first_frame,
            max_reference_images=caps.max_reference_images,
            text_to_video=caps.text_to_video,
        )
        if not satisfied:
            raise _video_bucket_capability_missing(generation_type, provider_id, model_id)

    async def _resolve_effective_video_provider_model(
        self,
        session: AsyncSession,
        selected: ProviderModel,
    ) -> ProviderModel:
        """Converge selected identities to actual video identities used for backend construction.

        Built-in registry identities are already effective; custom providers share loader rules
        (``lib.custom_provider.default_models.requested_model``): missing/disabled models or endpoints changed to other
        media_type fall back to default enabled video models. Discontinued corresponding catalog entries raise ``ModelDiscontinuedError`` without fallback.

        Materialized identities never converge here (see ``_resolve_video_provider_model``): changing execution models would
        silently switch models.
        """
        if not is_custom_provider(selected.provider_id):
            return selected

        # Lazy import: layering (pyproject.toml [tool.importlinter]) places lib.config below assembly;
        # this symbol's assembly layer depends on lib.config, so module-level imports would cycle. Built-ins do not use it,
        # avoiding loading the entire assembly layer for them.
        from lib.custom_provider.default_models import requested_model, resolve_default_model

        try:
            db_pid = parse_provider_id(selected.provider_id)
        except ValueError as exc:
            raise ValueError(f"invalid custom provider_id: {selected.provider_id}") from exc
        model = await requested_model(
            session, provider_id=selected.provider_id, db_id=db_pid, model_id=selected.model_id, media_type="video"
        )
        if model is not None:
            return selected

        logger.warning(
            "自定义模型 %s/%s 已不存在 / 已禁用 / 媒体类型不符（期望 video），身份解析回退到默认模型",
            selected.provider_id,
            selected.model_id,
        )
        try:
            default_model = await resolve_default_model(
                session, provider_id=selected.provider_id, db_id=db_pid, media_type="video"
            )
        except ValueError as exc:
            raise ValueError(f"custom model not found: {selected.provider_id}/{selected.model_id}") from exc
        return ProviderModel(selected.provider_id, default_model.model_id)

    async def _resolve_default_audio_backend(self, svc: ConfigService, session: AsyncSession) -> tuple[str, str]:
        """Resolve global audio defaults only: global default keys > automatic inference.

        Use four-level skeleton without projects (project=None skips project layers).
        """
        return await self._resolve_layered_backend(svc, session, None, _AUDIO_LAYERED_KEYS)

    async def _resolve_audio_provider_model(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project: dict | None,
        payload: dict | None,
    ) -> ProviderModel:
        """Three-level audio ProviderModel resolution: payload > project > global defaults.

        Payload layers drain historical tasks carrying ``audio_provider`` + ``audio_model``. Payload
        providers must be known (see ``_trusted_payload_provider``), otherwise fall back to project/global.
        """
        if payload:
            provider_id = _trusted_payload_provider(payload.get("audio_provider"))
            if provider_id is not None:
                model = await _payload_model_or_default(session, payload.get("audio_model"), provider_id, "audio")
                if model is not None:
                    return ProviderModel(provider_id, model)
        provider_id, model_id = await self._resolve_layered_backend(svc, session, project, _AUDIO_LAYERED_KEYS)
        return ProviderModel(provider_id, model_id)

    async def _resolve_video_capabilities_from_project(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project: dict | None,
        *,
        generation_type: VideoGenerationType | None = None,
    ) -> dict:
        """Resolve actual execution models by task buckets (project generation_mode when unspecified), then read capabilities.

        Share execution ``_resolve_video_provider_model`` including capability gates, never alternate reader semantics: changing
        generation_mode changes capability buckets. Missing bucket capabilities/invalid references raise without silently switching models
        (``docs/adr/0054``). Pass None payload: capability queries describe current configuration, never drain historical tasks.

        Pass selected identities only: ``_resolve_video_caps_for_model`` handles effective-identity convergence centrally; doing it here first
        adds duplicate custom-provider model queries.
        """
        if generation_type is None:
            generation_type = video_bucket_for_generation_mode(caps_generation_mode(project))
        selected = await self._resolve_video_provider_model(svc, session, project, None, generation_type)
        return await self._resolve_video_caps_for_model(
            svc, session, selected.provider_id, selected.model_id, project, generation_type=generation_type
        )

    async def _resolve_video_caps_for_model(
        self,
        svc: ConfigService,
        session: AsyncSession,
        provider_id: str,
        model_id: str,
        project: dict | None,
        *,
        generation_type: VideoGenerationType | None = None,
        builtin_endpoint: str | None = None,
    ) -> dict:
        # Output-track shapes branch by execution subpaths (Kling v3-omni multi-image subject paths omit audio switches), so capabilities need
        # actual buckets. Without explicit buckets, follow project routes, matching `_resolve_video_capabilities_from_project`
        # rules.
        if generation_type is None:
            generation_type = video_bucket_for_generation_mode(caps_generation_mode(project))
        effective = await self._resolve_effective_video_provider_model(session, ProviderModel(provider_id, model_id))
        provider_id, model_id = effective.provider_id, effective.model_id
        if is_custom_provider(provider_id):
            # Lazy import: layering (pyproject.toml [tool.importlinter]) places lib.config below assembly;
            # this symbol's assembly layer depends on lib.config, so module-level imports would cycle. Registry branches do not use it,
            # avoiding loading the entire assembly layer for them.
            from lib.custom_provider.capabilities import (
                overridden_audio_track,
                synthesize_video_capabilities_with_overrides,
            )
            from lib.custom_provider.model_facts import comfyui_duration_override, video_model_facts

            source = "custom"
            try:
                db_pid = parse_provider_id(provider_id)
            except ValueError as exc:
                raise ValueError(f"invalid custom provider_id: {provider_id}") from exc
            repo = CustomProviderRepository(session)
            model = await repo.get_model_by_ids(db_pid, model_id)
            if model is None:
                raise ValueError(f"custom model not found after identity resolution: {provider_id}/{model_id}")

            # Sole effective-capability composition (system decisions ⊕ user overrides): factories inject its same results into execution,
            # keeping display/execution strictly aligned. Never merge overrides or recompute system decisions here. Pure functions never query
            # provider rows or construct SDK clients, so per-unit video resolution has no DB/network/client-construction side effects
            # (nor missing-api_key errors).
            try:
                from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
                from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository

                endpoint_spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
                caps, applied_overrides = synthesize_video_capabilities_with_overrides(
                    endpoint=model.endpoint,
                    model_id=model_id,
                    overrides=model.capability_overrides,
                    endpoint_spec=endpoint_spec,
                    entry_binding=model.entry_binding,
                )
            except ValueError as exc:
                raise ValueError(f"cannot resolve video capabilities for {provider_id}/{model_id}: {exc}") from exc
            max_reference_images = caps.max_reference_images
            text_to_video = caps.text_to_video
            first_frame = caps.first_frame
            last_frame = caps.last_frame
            reference_audio_mode = caps.reference_audio_mode
            max_reference_audio_count = caps.max_reference_audio_count
            reference_audio_per_image = caps.reference_audio_per_image
            # Custom providers have no execution tiers: default tiers never narrow pricing parameters, so retain project request values.
            default_tier_generates_audio = True
            # Custom-provider track shapes use user capability overrides only (docs/adr/0104), matching default_tier_generates_audio
            # above: absent overrides assume audio, never invent genuinely silent models.
            audio_track = overridden_audio_track(caps, applied_overrides, generation_type)
            has_audio = audio_track != "always_off" if audio_track is not None else True
            # ComfyUI endpoint durations can entirely lie outside ArcReel control (``docs/adr/0082``): unbound ``frames``
            # or unreadable frame-rate sources mean empty model-row tiers. Omit submission durations and let workflows output their own
            # tiers. This is valid for this protocol, exempt from empty-set-fail-loud below: ADR 0018 protects
            # missing model declarations, while here the dimension does not exist at this endpoint. Other protocols still fail loudly.
            durations_optional = endpoint_spec.duration_tier_optional
            if endpoint_spec.endpoint_durations is not None:
                # ComfyUI endpoint durations derive only from node bindings (docs/adr/0082), never entries/name fallback. User additions/removals beyond native workflow
                # durations persist as ``durations`` overrides, the only accepted override for these endpoints (docs/adr/0104).
                # Endpoints remain tier truth: if endpoints cannot provide tiers (empty sets), invalidate all overrides, disable duration controls,
                # and let script planning borrow fixed volume.
                override = comfyui_duration_override(model.capability_overrides, model_id=model_id)
                endpoint_durations = list(endpoint_spec.endpoint_durations)
                supported_durations = override if override and endpoint_durations else endpoint_durations
            else:
                # Other endpoints: capability overrides > corresponding entries > name-inferred presets without corresponding entries. Calculate fallbacks here only,
                # never write fields back (docs/adr/0105).
                supported_durations = list(
                    video_model_facts(model_id, model.capability_overrides, entry_binding=model.entry_binding).durations
                )
        else:
            source = "registry"
            durations_optional = False
            if provider_id not in PROVIDER_REGISTRY:
                raise ValueError(f"provider not in PROVIDER_REGISTRY: {provider_id}")
            model_info = catalog.get_model(provider_id, model_id)
            if model_info is None:
                raise ValueError(f"model not found in registry: {provider_id}/{model_id}")
            supported_durations = list(model_info.supported_durations or [])
            # Effective video capability bits/reference-image limits evaluate channel declarations and model entries together, sharing execution gates,
            # capability gates (`_ensure_video_bucket_capability`) and bucket dropdowns (`lib.backends.generation_type_buckets`);
            # explicit `builtin_endpoint` evaluates backend-construction endpoints; other reads use current model endpoint associations.
            try:
                builtin_caps = builtin_video_capabilities_for_model(provider_id, model_id, endpoint=builtin_endpoint)
            except ValueError as exc:
                raise ValueError(f"cannot resolve video capabilities for {provider_id}/{model_id}: {exc}") from exc
            max_reference_images = builtin_caps.max_reference_images
            text_to_video = builtin_caps.text_to_video
            first_frame = builtin_caps.first_frame
            last_frame = builtin_caps.last_frame
            reference_audio_mode = builtin_caps.reference_audio_mode
            max_reference_audio_count = builtin_caps.max_reference_audio_count
            reference_audio_per_image = builtin_caps.reference_audio_per_image
            # Audio tracks share the same VideoCapabilities, but select paths by task buckets: silent reference-route subpaths
            # (Kling v3-omni multi-image subjects) truthfully derive voice_consistency=none.
            has_audio = builtin_caps.audio_track_for_route(generation_type) != "always_off"
            try:
                default_tier_generates_audio = builtin_effective_generate_audio_for_model(
                    provider_id, model_id, endpoint=builtin_endpoint
                )
            except ValueError as exc:
                raise ValueError(
                    f"cannot resolve video pricing capabilities for {provider_id}/{model_id}: {exc}"
                ) from exc

        if not supported_durations and not durations_optional:
            raise VideoSupportedDurationsError(
                "missing",
                provider_id=provider_id,
                model_id=model_id,
                message=f"supported_durations is empty for {provider_id}/{model_id}; cannot derive capabilities",
            )

        # Empty sets mean ``max_duration`` 0, synonymous with ``supported_durations == []``: ArcReel does not drive this
        # dimension; consumers must derive no selectable tiers, and UI disables duration controls.
        max_duration = max(supported_durations, default=0)
        duration_endpoint_fixed = not supported_durations and durations_optional

        # requested_generate_audio is **user silence intent** (global settings ← project.json overrides), sharing video_generate_audio
        # with execution MediaGenerator. generate_audio below is **pricing semantics** (including always-audio billing and default-execution-tier
        # decisions), not equivalent. Expose both for consumers to use by meaning: deciding whether this episode assembles
        # reference audio must read only the former.
        requested_generate_audio = await self._resolve_video_generate_audio_from_project(svc, project)
        # Always-audio-billed providers ignore request values. Other backend pricing parameters are True only when project requests enable audio
        # and context-free capabilities confirm default execution tiers produce voices.
        generate_audio = (
            True
            if _video_audio_always_billed(provider_id)
            else requested_generate_audio and default_tier_generates_audio
        )

        default_duration: int | None = None
        content_mode: str | None = None
        # Episode target durations are not model capabilities. Return alongside capabilities only because, like default_duration, they are project preferences
        # relevant to duration decisions: script-planning subagents obtain all needed inputs with one get_video_capabilities,
        # avoiding extra tool round trips for one preference. Read-time guards live in lib.episode.episode_target_duration.
        episode_target_duration = project_episode_target_duration(project)
        if project is not None:
            raw_default = project.get("default_duration")
            if isinstance(raw_default, int):
                default_duration = raw_default
            elif isinstance(raw_default, str) and raw_default.strip().isdigit():
                default_duration = int(raw_default.strip())
            cm = project.get("content_mode")
            if isinstance(cm, str) and cm:
                content_mode = cm
        generation_mode = caps_generation_mode(project)

        voice_consistency = derive_voice_consistency(
            reference_audio_mode=reference_audio_mode,
            generation_mode=generation_mode,
            has_audio=has_audio,
            character_voice_binding=character_voice_binding(project),
        )

        return {
            "provider_id": provider_id,
            "model": model_id,
            "supported_durations": supported_durations,
            "max_duration": max_duration,
            "max_reference_images": max_reference_images,
            "text_to_video": text_to_video,
            "first_frame": first_frame,
            "last_frame": last_frame,
            "generate_audio": generate_audio,
            "requested_generate_audio": requested_generate_audio,
            "max_reference_audio_count": max_reference_audio_count,
            "reference_audio_per_image": reference_audio_per_image,
            "source": source,
            # Empty tiers accepted by endpoints mean endpoint-fixed dimensions (GLOSSARY.md endpoint-fixed dimension).
            # Consumers distinguish absent endpoint dimensions from missing tier declarations, which already failed loudly above.
            "duration_endpoint_fixed": duration_endpoint_fixed,
            "duration_endpoint_fixed_reason": duration_endpoint_fixed_reason(duration_endpoint_fixed),
            "default_duration": default_duration,
            "episode_target_duration": episode_target_duration,
            "content_mode": content_mode,
            "generation_mode": generation_mode,
            "voice_consistency": voice_consistency,
        }

    async def _resolve_default_image_backend(
        self, svc: ConfigService, session: AsyncSession, generation_type: Literal["t2i", "i2i"] = "t2i"
    ) -> tuple[str, str]:
        """Resolve global image defaults only: global buckets > global default keys > automatic inference.

        Use four-level skeleton without projects (project=None skips project layers). Empty global buckets fall back to global default keys.
        """
        return await self._resolve_layered_backend(svc, session, None, _IMAGE_LAYERED_KEYS[generation_type])

    async def _resolve_provider_config(
        self,
        svc: ConfigService,
        session: AsyncSession,
        provider_id: str,
    ) -> dict[str, str]:
        config, _credential_id = await self._resolve_provider_config_and_credential(svc, session, provider_id)
        return config

    async def _resolve_provider_config_and_credential(
        self,
        svc: ConfigService,
        session: AsyncSession,
        provider_id: str,
    ) -> tuple[dict[str, str], int | None]:
        config = await svc.get_provider_config(provider_id)
        cred_repo = CredentialRepository(session)
        active = await cred_repo.get_active(provider_id)
        if active:
            # Credential overlays check Vertex credential-file existence; run in worker threads.
            await asyncio.to_thread(active.overlay_config, config)
        return config, active.id if active else None

    async def _resolve_reference_payload_limits(
        self,
        svc: ConfigService,
        session: AsyncSession,
        provider_id: str,
    ) -> tuple[int, int]:
        try:
            cfg = await self._resolve_provider_config(svc, session, provider_id)
        except ValueError:
            # Unknown/custom providers (_validate_provider raises ValueError) → conservative shared defaults.
            return DEFAULT_REFERENCE_TOTAL_MAX_BYTES, DEFAULT_REFERENCE_SINGLE_MAX_BYTES
        total = _parse_int(cfg.get(_REFERENCE_TOTAL_MAX_BYTES_KEY), DEFAULT_REFERENCE_TOTAL_MAX_BYTES)
        single = _parse_int(cfg.get(_REFERENCE_SINGLE_MAX_BYTES_KEY), DEFAULT_REFERENCE_SINGLE_MAX_BYTES)
        return total, single

    async def _resolve_all_provider_configs(
        self,
        svc: ConfigService,
        session: AsyncSession,
    ) -> dict[str, dict[str, str]]:
        configs = await svc.get_all_provider_configs()
        cred_repo = CredentialRepository(session)
        active_creds = await cred_repo.get_active_credentials_bulk()

        def overlay_all() -> None:
            for provider_id, cred in active_creds.items():
                cred.overlay_config(configs.setdefault(provider_id, {}))

        # Credential overlays check Vertex credential-file existence; thread the entire batch.
        await asyncio.to_thread(overlay_all)
        return configs

    async def default_text_backend(self) -> tuple[str, str]:
        """Return (provider_id, model_id)."""
        async with self._open_session() as (_session, svc):
            return await svc.get_default_text_backend()

    async def text_backend_for_task(
        self,
        task_type: TextTaskType,
        project_name: str | None = None,
    ) -> tuple[str, str]:
        """Resolve text backends by task tiers.

        Project-first priority: project tiers > project default models > global tiers > global default models > automatic inference.
        Vision-required tasks validate resolved capabilities, failing directly without silently switching models (docs/adr/0051).
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_text_backend(svc, session, task_type, project_name)

    async def _resolve_text_backend(
        self,
        svc: ConfigService,
        session: AsyncSession,
        task_type: TextTaskType,
        project_name: str | None,
    ) -> tuple[str, str]:
        project = await _load_project_snapshot(project_name)
        keys = _TEXT_LAYERED_KEYS[TEXT_TASK_TIERS[task_type]]
        resolved = await self._resolve_layered_backend(svc, session, project, keys)
        if task_type in VISION_REQUIRED_TASKS:
            await self._ensure_text_model_vision_capable(session, task_type, *resolved)
        return resolved

    async def _ensure_text_model_vision_capable(
        self, session: AsyncSession, task_type: TextTaskType, provider_id: str, model_id: str
    ) -> None:
        """Validate resolved models accept image inputs; fail directly without silently switching models.

        Built-ins use catalogs; custom providers use capability overrides > corresponding entries. Without matching entries or overrides,
        impose no constraints and defer to provider APIs, never guess.
        """
        if not is_custom_provider(provider_id):
            _ensure_text_model_vision_capable(task_type, provider_id, model_id)
            return
        from lib.custom_provider.model_facts import text_model_facts

        try:
            db_pid = parse_provider_id(provider_id)
        except ValueError:
            return
        model = await CustomProviderRepository(session).get_model_by_ids(db_pid, model_id)
        if (
            model is not None
            and text_model_facts(model_id, model.capability_overrides, entry_binding=model.entry_binding).image_input
            is False
        ):
            raise VisionCapabilityError(task_type=task_type, provider_id=provider_id, model_id=model_id)

    async def _resolve_auto_backends(
        self, svc: ConfigService, session: AsyncSession
    ) -> dict[AutoResolvePurpose, tuple[str, str] | None]:
        """Invoke automatic inference with purpose-specific keys, matching execution inputs when resolution reaches that layer."""
        resolved: dict[AutoResolvePurpose, tuple[str, str] | None] = {}
        for purpose, keys in _AUTO_RESOLVE_PURPOSE_KEYS.items():
            try:
                # Process sequentially: shared sessions do not support concurrent queries.
                resolved[purpose] = await self._auto_resolve_backend(
                    svc, session, keys.media_type, keys.generation_type
                )
            except ValueError:
                resolved[purpose] = None
        return resolved

    async def _auto_resolve_backend(
        self,
        svc: ConfigService,
        session: AsyncSession,
        media_type: str,
        generation_type: str | None = None,
    ) -> tuple[str, str]:
        """Traverse PROVIDER_REGISTRY in registration order, selecting the first ready provider supporting media_type.

        Custom-provider fallback and default fallback share bucket filtering (``lib.custom_provider.default_models``): with buckets,
        only matching bucket-default models qualify, otherwise t2i/i2i silently select the same row. Across providers, choose the first
        candidate (defaults per provider are normal, unlike two defaults in one provider/bucket).
        """
        statuses = await svc.get_all_providers_status()
        ready = {s.name: s for s in statuses if s.status == "ready"}

        for provider_id in PROVIDER_REGISTRY:
            if provider_id not in ready:
                continue
            # Provider-level defaults apply to automatic selection only without default-model settings; catalogs omit discontinued defaults and user-disabled ones are skipped too.
            model_id = catalog.default_model(provider_id, media_type)
            if model_id is not None and model_id not in ready[provider_id].disabled_models:
                return provider_id, model_id

        # Lazy import: layering (pyproject.toml [tool.importlinter]) places lib.config below assembly;
        # these symbols' assembly layer depends on lib.config, so module-level imports would cycle.
        from lib.custom_provider import make_provider_id
        from lib.custom_provider.default_models import callable_models, filter_by_bucket
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        repo = CustomProviderRepository(session)
        custom_models = await repo.list_enabled_models_by_media_type(media_type)
        # Custom-provider defaults also skip discontinued corresponding catalog entries.
        defaults = callable_models([model for model in custom_models if model.is_default], media_type)
        candidates = await filter_by_bucket(session, defaults, generation_type)
        if candidates:
            return make_provider_id(candidates[0].provider_id), candidates[0].model_id

        raise NoAvailableProviderError(
            f"未找到可用的 {media_type} 供应商。请在「全局设置 → 供应商」页面配置至少一个供应商。"
        )
