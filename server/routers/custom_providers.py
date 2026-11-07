"""Custom-provider management API.

Endpoints for CRUD, model management/discovery, and connectivity checks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel, Field, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from arcreel_market_core.video_backend_contract import ReferenceAudioMode, audio_capability_pair_is_coherent
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.backends.image_backends.base import ImageCapability
from lib.catalog.model_id import has_dot_segment
from lib.catalog.query import binding_key, find_bound_entry
from lib.config.model_setting_keys import PROJECT_MODEL_SETTING_KEYS
from lib.config.repository import mask_secret
from lib.custom_provider import is_custom_endpoint, make_provider_id
from lib.custom_provider.capabilities import (
    AUDIO_OVERRIDE_KEYS,
    AUDIO_TRACK_OVERRIDE_KEYS,
    CHANNEL_FACT_OVERRIDE_KEYS,
    filter_valid_overrides,
    resolve_audio_pair,
    strip_incoherent_audio_overrides,
    two_sided_video_capabilities,
)
from lib.custom_provider.capability_view import (
    CapabilityView,
    custom_capability_view,
    overridable_keys,
    override_value_check,
)
from lib.custom_provider.discovery_formats import endpoint_attachment_holds, is_comfyui_protocol
from lib.custom_provider.endpoint_resolution import endpoint_spec_from_row, resolve_endpoint_spec
from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    EndpointSpec,
    endpoint_spec_to_dict,
    get_endpoint_spec,
    infer_endpoint,
    static_media_type,
)
from lib.custom_provider.model_facts import (
    ALL_MODEL_FACT_OVERRIDE_KEYS,
    COMFYUI_VIDEO_OVERRIDE_KEYS,
    comfyui_duration_override,
    matched_entry_summary,
    model_fact_overrides,
    model_fact_type_name,
    model_fact_value_matches,
)
from lib.custom_provider.path_overrides import BaseUrlRule, effective_path_overrides, path_problem
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.custom_provider import CustomProviderModel
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.infra.api_errors import BadRequestError
from lib.infra.async_thread import run_sync_transaction
from server.i18n import Translator


def _validate_endpoint(value: str) -> str:
    """Pydantic validates built-in keys or custom-key shape; asynchronous write transactions check existence."""
    if value not in ENDPOINT_REGISTRY and not is_custom_endpoint(value):
        raise ValueError(f"unknown endpoint: {value!r}")
    return value


# Write-side endpoint fields share runtime checks; key sets track ENDPOINT_REGISTRY automatically.
# Responses need no validation, just str.
EndpointType = Annotated[str, AfterValidator(_validate_endpoint)]
DiscoveryFormatLiteral = Literal["openai", "google", "comfyui"]

# Typed concurrency cap: nullable positive integer (≥1); None means unset, falling back to global capacity defaults.
MaxWorkers = Annotated[int | None, Field(default=None, ge=1)]

# User-overridable dimensions (docs/adr/0104): model facts (text image input/structured output/max tokens; image
# text-to-image/image-to-image; video duration options/resolution/duration coupling) and video channel facts (input mode, reference/prompt-length
# limits, audio-track shape). Replace dimensions wholly at highest priority; persist no unlisted keys, even valid field names.
CAPABILITY_OVERRIDE_ALLOWLIST = CHANNEL_FACT_OVERRIDE_KEYS | ALL_MODEL_FACT_OVERRIDE_KEYS

#: Do not echo system decisions for two audio-track dimensions: without overrides, custom providers use no-signal/no-tightening and omit derivation
#: (``lib.config.resolver``); displaying static channel declarations would suggest they apply to custom models.
_AUDIO_TRACK_FIELDS = AUDIO_TRACK_OVERRIDE_KEYS


#: Image-model override key → endpoint-declared capability.
_IMAGE_CAPABILITY_BY_OVERRIDE_KEY: dict[str, ImageCapability] = {
    "text_to_image": ImageCapability.TEXT_TO_IMAGE,
    "image_to_image": ImageCapability.IMAGE_TO_IMAGE,
}


def _comfyui_override_keys(media_type: str) -> frozenset[str]:
    """Accepted ComfyUI override dimensions: video-row duration options only (docs/adr/0104)."""
    return COMFYUI_VIDEO_OVERRIDE_KEYS if media_type == "video" else frozenset()


def _narrow_to_allowlist(overrides: dict[str, object]) -> dict[str, object]:
    """Narrow to the open allowlist, shared by writes/readback with one key-set source instead of separately derived sets kept consistent only by comments."""
    return {k: v for k, v in overrides.items() if k in CAPABILITY_OVERRIDE_ALLOWLIST}


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-providers", tags=["Custom Providers"])

_CONNECTIVITY_CHECK_TIMEOUT = 15  # Seconds.

#: ComfyUI connectivity probe path, anonymously accessible on unauthenticated servers, also verifying correct addresses.
_COMFYUI_SYSTEM_STATS_PATH = "/system_stats"

# Global settings potentially referencing custom providers; clean dangling references on provider/model deletion.
_BACKEND_SETTING_KEYS = (
    "default_video_backend",
    "default_image_backend",
    "default_image_backend_t2i",
    "default_image_backend_i2i",
    "default_text_backend",
    "default_audio_backend",
    "text_backend_simple",
    "text_backend_complex",
)

# Project overrides differ from global names: resolver reads video_backend /
# audio_backend / image_provider_* by media; text-tier/project-default keys match global names. Use this set for project cleanup.
# Deliberately exclude video buckets (video_provider_i2v / video_provider_r2v):
# _ensure_video_bucket_capability reports dangling references, without cascading write cleanup (docs/adr/0054). Image buckets lack that gate,
# so still clean them here.
_PROJECT_BACKEND_KEYS = (
    "video_backend",
    "audio_backend",
    "image_provider_t2i",
    "image_provider_i2i",
    "default_image_backend",
    "text_backend_simple",
    "text_backend_complex",
    "default_text_backend",
)

# All global custom-model references for ID renaming. Separate from _BACKEND_SETTING_KEYS,
# which defines deletion cascades excluding video buckets under ADR 0054; ID changes must still update them.
_GLOBAL_BUCKET_REFERENCE_KEYS = (
    *_BACKEND_SETTING_KEYS,
    "default_video_backend_i2v",
    "default_video_backend_r2v",
)

# ---------------------------------------------------------------------------
# Pydantic models.
# ---------------------------------------------------------------------------


class ModelInput(BaseModel):
    model_id: str
    display_name: str
    endpoint: EndpointType
    is_default: bool = False
    is_enabled: bool = True
    resolution: str | None = None
    # Sparse model/video-channel fact overrides (docs/adr/0104); None/absent keys follow entry/channel declarations.
    # Model-list saving replaces all: roundtrip this field or existing overrides are cleared.
    capability_overrides: dict[str, object] | None = None
    # Path overrides: request name → whole replacement path (docs/adr/0101); roundtrip with lists like capability_overrides.
    # Empty dictionaries normalize to None.
    path_overrides: dict[str, str] | None = None

    @field_validator("path_overrides")
    @classmethod
    def _empty_path_overrides_to_none(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return value or None

    # Provider-model multiplier (docs/adr/0100), multiplied by provider multiplier over the full reference charge.
    multiplier: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    # Entry binding (docs/adr/0105): model vendor/official name; None matches by name. Roundtrip with lists like overrides.
    entry_binding: str | None = None

    @field_validator("entry_binding")
    @classmethod
    def _entry_binding_shape(cls, value: str | None) -> str | None:
        """Validate only entry-binding shape (model vendor/official model name), not catalog existence. After
        official entry removal, whole-table PUT must not block unrelated edits at 422. Readers fall back to
        name matching for missing bindings."""
        value = (value or "").strip() or None
        if value is not None and binding_key(value, "text") is None:
            raise ValueError("entry_binding must look like 'maker/name'")
        return value

    @field_validator("capability_overrides")
    @classmethod
    def _keep_open_capabilities_only(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        """Silently remove override keys outside the open allowlist; empty dictionaries become None.

        Such keys arise only from manual DB edits. UI neither shows nor removes them, so rejecting saves
        would trap users in unfixable 422 even for display-name changes. Removal is their only exit, ensuring
        persisted overrides contain currently open dimensions. Silent to users, but log a warning as the only
        evidence of discarded data for operations investigating vanished manual DB values."""
        if not value:
            return None
        kept = _narrow_to_allowlist(value)
        if dropped := sorted(value.keys() - kept.keys()):
            logger.warning("能力覆盖含未开放键，保存时已剔除: %s", ", ".join(dropped))
        return kept or None

    def to_db_dict(self, endpoint_spec: EndpointSpec) -> dict:
        """Return a DB-ready dictionary.

        Prices are not model-row data: use matched-entry price tables (docs/adr/0106). Durations/max-output
        tokens persist only as overrides, without save-time prefilling; presets serve new-entry prefills and
        runtime fallbacks only (``docs/adr/0105``).

        ComfyUI video duration options default to workflow native duration; user-added/removed tiers become
        ``durations`` overrides (``docs/adr/0082``, ``0104``). Omit when matching endpoint-derived tiers; always
        omit if endpoints cannot drive duration (empty tier set), leaving this dimension workflow-fixed.

        Read media type from caller-resolved specs: ``ce-`` definitions own their media type, uninferable from key prefixes."""
        d = self.model_dump()
        overrides = d.get("capability_overrides")
        if endpoint_spec.kind == COMFYUI_KIND and isinstance(overrides, dict) and "durations" in overrides:
            endpoint_durations = endpoint_spec.endpoint_durations or []
            durations = overrides.get("durations")
            if not endpoint_durations or (
                isinstance(durations, list) and sorted(set(durations)) == sorted(set(endpoint_durations))
            ):
                overrides = {k: v for k, v in overrides.items() if k != "durations"}
            d["capability_overrides"] = overrides or None
        return d


class CreateProviderRequest(BaseModel):
    display_name: str
    discovery_format: DiscoveryFormatLiteral
    base_url: str
    api_key: str
    models: list[ModelInput] = []
    image_max_workers: MaxWorkers
    video_max_workers: MaxWorkers
    audio_max_workers: MaxWorkers


class UpdateProviderRequest(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class FullUpdateProviderRequest(BaseModel):
    """Full PUT update: provider metadata plus optional model list.

    Omitted ``models`` (``None``) leaves models unchanged. Provider-detail models save individually via
    model APIs; whole-table readback would overwrite intervening per-row enablement/settings. Supplied lists replace all."""

    display_name: str
    base_url: str
    api_key: str | None = None  # None means unchanged.
    models: list[ModelInput] | None = None
    # PUT always supplies concurrency caps (empty → None → global default); None clears, unlike unchanged semantics.
    image_max_workers: MaxWorkers
    video_max_workers: MaxWorkers
    audio_max_workers: MaxWorkers


class ConnectivityCheckRequest(BaseModel):
    # Connectivity deliberately accepts arbitrary strings; _run_connectivity_check soft-fails with 200 + success=False.
    discovery_format: str
    base_url: str
    api_key: str


class EndpointRequestDescriptor(BaseModel):
    """One endpoint request with overridable path, aligned with lib.custom_provider.path_overrides.EndpointRequest."""

    name: str
    # Frontend dashboard i18n key.
    display_name_key: str
    default_path: str
    placeholders: list[str]
    base_url_rule: BaseUrlRule


class ModelResponse(BaseModel):
    id: int
    model_id: str
    display_name: str
    endpoint: str
    is_default: bool
    is_enabled: bool
    resolution: str | None = None
    # Full four-field system decisions, video endpoints only; None for nonvideo or invalid declarations.
    system_capabilities: dict[str, object] | None = None
    # Sparse user overrides; straightforward merge with system_capabilities yields effective values.
    capability_overrides: dict[str, object] | None = None
    # Effective path overrides (docs/adr/0101): omit request names no longer defined, discarded next save.
    path_overrides: dict[str, str] | None = None
    # Overridable endpoint requests (name, display key, default path) for request-address UI.
    requests: list[EndpointRequestDescriptor] = []
    # Echo provider-model multiplier/binding unchanged for whole-table PUT.
    multiplier: float = 1.0
    entry_binding: str | None = None
    # Matched-entry summary (maker / name / media_type / display_name / priced); None without matches,
    # recording calls as unpriced.
    matched_entry: dict[str, object] | None = None


class ProviderResponse(BaseModel):
    id: int
    display_name: str
    discovery_format: str
    base_url: str
    api_key_masked: str
    models: list[ModelResponse]
    created_at: str | None = None
    image_max_workers: int | None = None
    video_max_workers: int | None = None
    audio_max_workers: int | None = None


class ConnectivityCheckResponse(BaseModel):
    success: bool
    message: str
    model_count: int = 0


class DiscoverResponse(BaseModel):
    models: list[dict] = []
    # Protocol has no enumerable models. ComfyUI models are workflow node choices, without a list matching rows;
    # empty lists would mean none discovered and misleadingly prompt credential checks.
    # Frontend replaces discovery buttons with explanatory text using this flag.
    not_applicable: bool = False
    reason: str | None = None


class DiscoverAnthropicRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


class EndpointDescriptor(BaseModel):
    """One catalog-API endpoint descriptor for frontend use, aligned with lib.custom_provider.endpoints.EndpointSpec without closures."""

    key: str
    media_type: str
    family: str
    # Implementation: "python" (backend code) | "declarative" (definition) | "comfyui" (workflow
    # plus bindings). Frontend shows personal-copy/view-definition only for declarative endpoints and
    # filters endpoint selectors by protocol.
    kind: str
    # Origin: built-in bundled endpoints, uneditable/undeletable, or user custom_endpoint rows.
    # Frontend groups accordingly, permitting edit/delete only for custom.
    source: Literal["builtin", "custom"] = "builtin"
    display_name_key: str
    # Declarative display name from meta.name (proper names untranslated); Python built-ins use None
    # and frontend display_name_key translations. Exactly one applies; frontend prefers this field.
    display_name: str | None = None
    request_method: str
    request_path_template: str
    image_capabilities: list[str] | None = None  # Image types contain capability-string lists, others None.
    # Whether execution actually forwards end-frame constraints, relevant only to video. Narrow last_frame
    # forced-on override controls accordingly instead of users discovering 422 only on saving.
    end_image_capable: bool = False
    # Four parameter constraints, nondefault only for ComfyUI (``docs/adr/0082``): bindings determine whether ArcReel
    # can drive dimensions/duration; disable corresponding selectors with explanations if not. native_resolution is the workflow's
    # actual tier without selection, shown as resolution-selector empty placeholder.
    size_fixed: bool = False
    duration_fixed: bool = False
    # Second empty-tier cause: frames bound but no readable FPS source. Like duration_fixed, affects wording only.
    duration_frame_rate_missing: bool = False
    # No tiers (unbound frames, no FPS source, or no integer-second conversion) means ArcReel does not drive duration:
    # model-row tier editing is read-only and project duration controls hidden. The two flags above are subsets,
    # choosing fixed/missing-FPS/inconvertible-to-integer-seconds wording only.
    duration_tier_empty: bool = False
    native_resolution: str | None = None
    # Overridable requests (docs/adr/0101); empty list means no request-address editing.
    requests: list[EndpointRequestDescriptor] = []


class EndpointCatalogResponse(BaseModel):
    endpoints: list[EndpointDescriptor]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _system_capabilities_for(
    endpoint: str, model_id: str, endpoint_spec: EndpointSpec | None = None, entry_binding: str | None = None
) -> dict[str, object] | None:
    """Read computed model capabilities from endpoint declarations and model entries, without overrides; None for nonvideo endpoints.

    Evaluation failure (retired endpoints, bad registry declarations) degrades to None rather than 500:
    list endpoints must still render other models; one unresolved row only loses a computed-value hint.

    Do not echo the two audio-track dimensions (see ``_AUDIO_TRACK_FIELDS``)."""
    try:
        media_type = endpoint_spec.media_type if endpoint_spec is not None else static_media_type(endpoint)
        if media_type != "video":
            return None
        caps = asdict(
            two_sided_video_capabilities(
                endpoint=endpoint, model_id=model_id, endpoint_spec=endpoint_spec, entry_binding=entry_binding
            )
        )
        return {k: v for k, v in caps.items() if k not in _AUDIO_TRACK_FIELDS}
    except ValueError:
        logger.warning("无法判定系统能力: endpoint=%r model_id=%r", endpoint, model_id)
        return None


def _effective_overrides_for_response(
    endpoint: str,
    model_id: str,
    overrides: object | None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> dict[str, object] | None:
    """Filter readback using write-side criteria, dropping keys/values execution ignores and narrowing to the open allowlist.

    Legacy/non-API writes may leave incompatible overrides (e.g. last_frame=True after an endpoint loses
    end_image_capable). Echoing them shows effective overrides while execution silently ignores them;
    ordinary client save roundtrips then trigger write-side 422, blocking unrelated edits.

    Also narrow with :func:`_narrow_to_allowlist`: echoing legacy outside-allowlist keys (manual DB edits)
    would diverge from persisted keys and force callers to understand dirty readbacks stripped on saving.
    Use one shared narrowing function for both sets.

    Audio dimensions also pass :func:`strip_incoherent_audio_overrides`: individually valid but meaningless
    combinations (direct ⊕ limit 0) pass :func:`filter_valid_overrides`, yet execution reduces them to
    ``none``. Echoing originals would reproduce apparent effectiveness versus ignored execution.

    Echo only valid model-fact dimensions for the endpoint media type. Hide enabled image text-to-image/
    image-to-image overrides when endpoints lack support (writes reject the same combination). ComfyUI
    accepts/echoes only video-row duration-option overrides."""
    if endpoint_spec is None:
        try:
            endpoint_spec = get_endpoint_spec(endpoint)
        except ValueError:
            return None
    if endpoint_spec.kind == COMFYUI_KIND:
        if endpoint_spec.media_type != "video":
            return None
        durations = comfyui_duration_override(overrides, model_id=model_id)
        return {"durations": durations} if durations else None
    media_type = endpoint_spec.media_type
    filtered: dict[str, object] = {}
    if media_type == "video":
        filtered = strip_incoherent_audio_overrides(
            _narrow_to_allowlist(
                filter_valid_overrides(
                    endpoint=endpoint,
                    model_id=model_id,
                    overrides=overrides,
                    endpoint_spec=endpoint_spec,
                )
            ),
            endpoint=endpoint,
            model_id=model_id,
            endpoint_spec=endpoint_spec,
            entry_binding=entry_binding,
        )
    implemented = endpoint_spec.image_capabilities
    for key, value in model_fact_overrides(overrides, media_type, model_id=model_id).items():
        image_capability = _IMAGE_CAPABILITY_BY_OVERRIDE_KEY.get(key)
        if image_capability is not None and value is True and image_capability not in (implemented or frozenset()):
            continue
        filtered[key] = value
    return filtered or None


async def _read_endpoint_specs(
    session: AsyncSession,
    models,
    *,
    on_unknown: Callable[[str], None] | None = None,
) -> dict[str, EndpointSpec]:
    """Resolve each distinct endpoint spec once for all models.

    ``on_unknown`` controls failures: readback logs and degrades legacy dirty rows; writes provide a
    422-raising callback, preventing nonexistent endpoint references from persisting."""
    repo = CustomEndpointRepository(session)
    specs: dict[str, EndpointSpec] = {}
    for endpoint in {model.endpoint for model in models}:
        try:
            specs[endpoint] = await resolve_endpoint_spec(endpoint, repo.get)
        except ValueError:
            if on_unknown is not None:
                on_unknown(endpoint)
            logger.warning("无法解析模型 endpoint，按存量脏配置回显: endpoint=%r", endpoint)
    return specs


def _model_to_response(
    m,
    endpoint_spec: EndpointSpec | None = None,
) -> ModelResponse:
    requests = endpoint_spec.requests if endpoint_spec is not None else ()
    try:
        media_type = endpoint_spec.media_type if endpoint_spec is not None else static_media_type(m.endpoint)
    except ValueError:
        media_type = None
    return ModelResponse(
        path_overrides=effective_path_overrides(m.path_overrides, requests) or None,
        requests=[EndpointRequestDescriptor(**asdict(r)) for r in requests],
        system_capabilities=_system_capabilities_for(m.endpoint, m.model_id, endpoint_spec, m.entry_binding),
        capability_overrides=_effective_overrides_for_response(
            m.endpoint, m.model_id, m.capability_overrides, endpoint_spec, m.entry_binding
        ),
        id=m.id,
        model_id=m.model_id,
        display_name=m.display_name,
        endpoint=m.endpoint,
        is_default=m.is_default,
        is_enabled=m.is_enabled,
        resolution=m.resolution,
        multiplier=m.multiplier,
        entry_binding=m.entry_binding,
        matched_entry=(
            matched_entry_summary(m.model_id, media_type, m.entry_binding) if media_type is not None else None
        ),
    )


def _provider_to_response(
    provider,
    models,
    endpoint_specs: Mapping[str, EndpointSpec] | None = None,
) -> ProviderResponse:
    return ProviderResponse(
        id=provider.id,
        display_name=provider.display_name,
        discovery_format=provider.discovery_format,
        base_url=provider.base_url,
        api_key_masked=mask_secret(provider.api_key),
        models=[
            _model_to_response(
                m,
                None if endpoint_specs is None else endpoint_specs.get(m.endpoint),
            )
            for m in models
        ],
        created_at=dt_to_iso(provider.created_at),
        image_max_workers=provider.image_max_workers,
        video_max_workers=provider.video_max_workers,
        audio_max_workers=provider.audio_max_workers,
    )


async def _clear_global_provider_refs(
    session: AsyncSession, provider_id: int, model_ids: set[str] | None = None
) -> None:
    """Clear global settings referencing this provider within _BACKEND_SETTING_KEYS only; do not commit.

    ``model_ids`` None clears any model under the provider (provider deletion); otherwise clear only
    those models (removed during saving)."""
    if model_ids is not None and not model_ids:
        return
    from lib.config.service import ConfigService

    svc = ConfigService(session)
    prefix = f"{make_provider_id(provider_id)}/"
    for key in _BACKEND_SETTING_KEYS:
        val = await svc.get_setting(key, "")
        if val.startswith(prefix) and (model_ids is None or val[len(prefix) :] in model_ids):
            await svc.set_setting(key, "")


async def _rename_global_provider_refs(session: AsyncSession, provider_id: int, old_id: str, new_id: str) -> None:
    """After model-ID changes, rewrite global old-ID references, including video bucket keys; do not commit."""
    from lib.config.service import ConfigService

    svc = ConfigService(session)
    prefix = make_provider_id(provider_id)
    old_ref, new_ref = f"{prefix}/{old_id}", f"{prefix}/{new_id}"
    for key in _GLOBAL_BUCKET_REFERENCE_KEYS:
        if await svc.get_setting(key, "") == old_ref:
            await svc.set_setting(key, new_ref)


def _mutate_all_projects(mutate: Callable[[dict], None]) -> None:
    """Apply ``mutate`` to every project.json; skip unreadable/unwritable projects as nonfatal."""
    from lib.config.resolver import get_project_manager

    pm = get_project_manager()
    for proj_name in pm.list_projects():
        try:
            pm.update_project(proj_name, mutate)
        except Exception:
            logger.warning("同步项目 %s 的模型引用失败，已跳过", proj_name, exc_info=True)


def _cleanup_project_refs(prefix: str, setting_keys: tuple[str, ...], model_ids: set[str] | None = None) -> None:
    """Clear project.json dangling references after provider/model deletion.

    ``model_ids`` None clears any model under the provider, otherwise specified models only. Always clear
    these models' ``model_settings`` entries regardless of ``setting_keys``: deleted-model resolution
    overrides are useless and would otherwise be inherited by later same-ID models."""
    if model_ids is not None and not model_ids:
        return

    def _hits(ref: object) -> bool:
        return (
            isinstance(ref, str) and ref.startswith(prefix) and (model_ids is None or ref[len(prefix) :] in model_ids)
        )

    def _mutate(p: dict) -> None:
        for key in setting_keys:
            if _hits(p.get(key, "")):
                p.pop(key, None)
        model_settings = p.get("model_settings")
        if isinstance(model_settings, dict):
            for ref in [r for r in model_settings if _hits(r)]:
                del model_settings[ref]

    _mutate_all_projects(_mutate)


def _rename_project_refs(prefix: str, old_id: str, new_id: str) -> None:
    """After model-ID changes, rewrite old-ID references in all projects: 10 reference keys plus ``model_settings`` keys."""
    old_ref, new_ref = f"{prefix}{old_id}", f"{prefix}{new_id}"

    def _mutate(p: dict) -> None:
        for key in PROJECT_MODEL_SETTING_KEYS:
            if p.get(key) == old_ref:
                p[key] = new_ref
        model_settings = p.get("model_settings")
        if isinstance(model_settings, dict) and old_ref in model_settings:
            model_settings[new_ref] = model_settings.pop(old_ref)

    _mutate_all_projects(_mutate)


async def cleanup_project_model_refs(provider_id: int, model_ids: set[str]) -> None:
    """After committed deletion, clear project references to these models (synchronous file I/O, offload to thread pool)."""
    if model_ids:
        await run_sync_transaction(
            _cleanup_project_refs, f"{make_provider_id(provider_id)}/", _PROJECT_BACKEND_KEYS, model_ids
        )


async def rename_project_model_refs(provider_id: int, old_id: str, new_id: str) -> None:
    """After committed model-ID changes, rewrite project references (synchronous file I/O, offload to thread pool)."""
    if old_id != new_id:
        await run_sync_transaction(_rename_project_refs, f"{make_provider_id(provider_id)}/", old_id, new_id)


def _check_duplicate_model_ids(models: list[ModelInput], _t: Callable[..., str]) -> None:
    """Validate unique API model names within providers, and valid model_id/endpoint for enabled models."""
    seen: set[str] = set()
    for m in models:
        if m.is_enabled and not m.model_id.strip():
            raise HTTPException(status_code=422, detail=_t("model_id_required"))
        if m.is_enabled and not m.endpoint:
            raise HTTPException(status_code=422, detail=_t("endpoint_required"))
        if has_dot_segment(m.model_id):
            raise HTTPException(status_code=422, detail=_t("model_id_dot_segment", model=m.model_id))
        if m.model_id in seen:
            raise HTTPException(status_code=422, detail=_t("duplicate_model_id", model_id=m.model_id))
        if m.model_id:
            seen.add(m.model_id)


def _check_capability_overrides(
    overrides: dict[str, object] | None,
    endpoint: str,
    model_id: str,
    _t: Callable[..., str],
    spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> None:
    """Write-side validation: composition only degrades bad values, so this function owns validity checks.

    None/empty dictionaries both follow all entry/channel declarations and are always accepted.
    ``ModelInput`` already narrows keys to the open allowlist. Check remaining endpoint constraints:
    ComfyUI accepts no overrides; keys must belong to endpoint media type (channel facts are video-only);
    value shapes must match dimensions; enabled overrides require actual endpoint implementation."""
    if not overrides:
        return
    resolved_spec = spec or get_endpoint_spec(endpoint)
    media_type = resolved_spec.media_type
    if resolved_spec.kind == COMFYUI_KIND:
        # ComfyUI capabilities derive only from node bindings (docs/adr/0082); overrides have no execution counterpart except video-row
        # duration preferences added/removed around workflow native durations (docs/adr/0104).
        if not overrides.keys() <= _comfyui_override_keys(media_type):
            raise HTTPException(
                status_code=422,
                detail=_t("capability_overrides_not_supported_for_comfyui", model_id=model_id),
            )
        if not model_fact_value_matches("durations", overrides["durations"]):
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_invalid_value",
                    model_id=model_id,
                    capability="durations",
                    expected=model_fact_type_name("durations"),
                ),
            )
        return
    applicable = overridable_keys(media_type)
    for key, value in overrides.items():
        if key not in applicable:
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_not_applicable",
                    model_id=model_id,
                    endpoint=endpoint,
                    media_type=media_type,
                    capability=key,
                ),
            )
        # Share value-type checks with readers; separate implementations drift, letting writes accept values composition
        # silently ignores, exactly the UI-allows/execution-retracts mismatch this override path eliminates.
        valid, expected_name = override_value_check(media_type, key, value)
        if not valid:
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_invalid_value",
                    model_id=model_id,
                    capability=key,
                    expected=expected_name,
                ),
            )
        # Image t2i/i2i overrides replace model facts, but effective support still intersects channel declarations:
        # reject enabled-but-unimplemented capabilities here, never display false enablement.
        if key in _IMAGE_CAPABILITY_BY_OVERRIDE_KEY and value is True:
            implemented = resolved_spec.image_capabilities or frozenset()
            if _IMAGE_CAPABILITY_BY_OVERRIDE_KEY[key] not in implemented:
                raise HTTPException(
                    status_code=422,
                    detail=_t(
                        "capability_override_image_unsupported",
                        model_id=model_id,
                        endpoint=endpoint,
                        capability=key,
                    ),
                )
        # last_frame=True requires endpoint delegate.generate() to actually read
        # end_image and forward end-frame constraints, not merely advertise support while silently generating unconstrained output.
        if key == "last_frame" and value is True and not resolved_spec.end_image_capable:
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_last_frame_unsupported",
                    model_id=model_id,
                    endpoint=endpoint,
                ),
            )
        # Likewise direct voice overrides require actual endpoint reference_audio_files forwarding.
        if (
            key == "reference_audio_mode"
            and value == ReferenceAudioMode.DIRECT.value
            and not resolved_spec.reference_audio_capable
        ):
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_reference_audio_unsupported",
                    model_id=model_id,
                    endpoint=endpoint,
                ),
            )

    # Merged invariant: individually valid but meaningless dimensions (voice input with limit 0) are rejected here,
    # not silently degraded at execution into impossible follow-up advice such as maximum 0 segments.
    # Sparse overrides may create this too; fill untouched dimensions from system decisions before checking.
    if any(key in overrides for key in AUDIO_OVERRIDE_KEYS):
        mode, count = resolve_audio_pair(
            overrides, endpoint=endpoint, model_id=model_id, endpoint_spec=resolved_spec, entry_binding=entry_binding
        )
        if not audio_capability_pair_is_coherent(mode=mode, count=count):
            raise HTTPException(
                status_code=422,
                detail=_t("capability_override_audio_pair_incoherent", model_id=model_id),
            )


def _check_model_capability_overrides(
    models: list[ModelInput],
    _t: Callable[..., str],
    specs: Mapping[str, EndpointSpec] | None = None,
) -> None:
    """Validate overrides per model in batch-save paths, including settings-form edits.

    Validate submitted ``(endpoint, 覆盖值)`` pairs: legality changes by endpoint (last_frame=True requires
    end_image_capable; nonvideo endpoints directly reject nonempty overrides). Thus whole-table PUT
    changing only endpoint still rechecks unchanged overrides against the new endpoint. Outside-allowlist
    keys never reach here: ``ModelInput`` strips them while parsing."""
    for m in models:
        _check_capability_overrides(
            m.capability_overrides,
            m.endpoint,
            m.model_id,
            _t,
            None if specs is None else specs[m.endpoint],
            m.entry_binding,
        )


def _check_model_path_overrides(
    models: list[ModelInput],
    specs: Mapping[str, EndpointSpec],
    _t: Callable[..., str],
) -> None:
    """Validate path overrides: request names must belong to current endpoints, paths start with ``/``, and placeholders stay within each request's allowed set.

    ComfyUI/requestless endpoints expose no overridable requests; reject any override as unknown request names."""
    for m in models:
        requests = {r.name: r for r in specs[m.endpoint].requests}
        for name, path in (m.path_overrides or {}).items():
            request = requests.get(name)
            if request is None:
                raise HTTPException(
                    status_code=422,
                    detail=_t("path_override_unknown_request", model_id=m.model_id, request=name),
                )
            if problem := path_problem(request, path):
                raise HTTPException(
                    status_code=422,
                    detail=_t(
                        problem,
                        model_id=m.model_id,
                        path=path,
                        allowed=", ".join(f"{{{{ {p} }}}}" for p in request.placeholders),
                    ),
                )


def _drop_rebound_path_overrides(m: ModelInput, stored: CustomProviderModel | None) -> ModelInput:
    """Discard existing path overrides when switching endpoints (docs/adr/0101).

    Whole-table PUT echoes originals. On endpoint changes, equal-name/equal-value stored entries are
    old overrides and discarded; retain newly entered entries for :func:`_check_model_path_overrides`
    validation against new endpoints."""
    if stored is None or stored.endpoint == m.endpoint or not m.path_overrides:
        return m
    original = stored.path_overrides or {}
    kept = {name: path for name, path in m.path_overrides.items() if original.get(name) != path}
    return m.model_copy(update={"path_overrides": kept or None})


def _check_protocol_constraints(
    models: list[ModelInput],
    discovery_format: str,
    specs: Mapping[str, EndpointSpec],
    _t: Callable[..., str],
) -> None:
    """Two ComfyUI-specific write constraints: bidirectional endpoint compatibility and disabled capability overrides.

    Compatibility is bidirectional (``docs/adr/0081``): ComfyUI runtime endpoints make sense only for
    ComfyUI services, and declarative endpoints are equally meaningless there. Save-time mismatches
    otherwise surface only in generation transport; reject both directions on writes. The predicate is
    :func:`endpoint_attachment_holds`; select only the offending direction here.

    Run before :func:`_check_model_capability_overrides`: empty ComfyUI capability bits also fail generic
    checks. Protocol-first validation yields dedicated unsupported-overrides wording instead of unsupported-end-frame wording.

    Overrides are disabled for this protocol: ComfyUI capabilities derive from node bindings, with no
    execution counterpart for overrides. Readback ignores legacy values (see
    :func:`lib.custom_provider.capabilities.filter_valid_overrides`).

    Apply only to comfyui; preserve other protocols' free attachments/override editing."""
    comfyui_provider = is_comfyui_protocol(discovery_format)
    for m in models:
        spec = specs[m.endpoint]
        if not endpoint_attachment_holds(endpoint_kind=spec.kind, discovery_format=discovery_format):
            key = (
                "comfyui_provider_requires_comfyui_endpoint"
                if comfyui_provider
                else "comfyui_endpoint_requires_comfyui_provider"
            )
            raise HTTPException(status_code=422, detail=_t(key, model_id=m.model_id, endpoint=m.endpoint))
        if (
            comfyui_provider
            and m.capability_overrides
            and not (
                spec.kind == COMFYUI_KIND and m.capability_overrides.keys() <= _comfyui_override_keys(spec.media_type)
            )
        ):
            raise HTTPException(
                status_code=422,
                detail=_t("capability_overrides_not_supported_for_comfyui", model_id=m.model_id),
            )


async def _resolve_model_endpoint_specs(
    session: AsyncSession,
    models: list[ModelInput],
    _t: Callable[..., str],
) -> dict[str, EndpointSpec]:
    """Resolve specs on writes: any nonexistent model-row endpoint yields 422, without persistence."""

    def reject(endpoint: str) -> None:
        raise HTTPException(status_code=422, detail=_t("unknown_endpoint", endpoint=endpoint))

    return await _read_endpoint_specs(session, models, on_unknown=reject)


def _check_unique_defaults(models: list[ModelInput], specs: dict[str, EndpointSpec], _t: Callable[..., str]) -> None:
    """Validate mutually exclusive default models.

    - Nonimage endpoints (text / video / audio): at most one is_default=True per media_type.
    - Image endpoints: pairwise disjoint capabilities (at most one default per capability).

    Read types/capabilities from resolved ``specs``: ``ce-`` definitions own both, not inferable by key
    prefixes, and built-in lookups always fail for them. :func:`_resolve_model_endpoint_specs` resolves
    ``specs`` now and already rejects unresolved rows with 422."""
    text_video_defaults: dict[str, list[str]] = {}
    image_defaults: list[tuple[str, frozenset[ImageCapability]]] = []
    for m in models:
        if not m.is_default:
            continue
        spec = specs[m.endpoint]
        if spec.media_type != "image":
            text_video_defaults.setdefault(spec.media_type, []).append(m.model_id)
            continue
        image_defaults.append((m.model_id, spec.image_capabilities or frozenset()))

    duplicates: dict[str, list[str]] = {mt: ids for mt, ids in text_video_defaults.items() if len(ids) > 1}

    # Image capability reverse index: >1 default per slot conflicts (O(n), replacing O(n²) pairwise intersections).
    cap_to_ids: dict[ImageCapability, list[str]] = {}
    for mid, caps in image_defaults:
        for c in caps:
            cap_to_ids.setdefault(c, []).append(mid)
    conflict_ids = [mid for ids in cap_to_ids.values() if len(ids) > 1 for mid in ids]
    # Empty capability sets mean undeclared support, indistinguishable from any image default: another default conflicts.
    # Saving two would appear valid until default lookups find two rows and fail generation.
    if len(image_defaults) > 1 and any(not caps for _mid, caps in image_defaults):
        conflict_ids = [mid for mid, _caps in image_defaults]
    if conflict_ids:
        duplicates["image"] = list(dict.fromkeys(conflict_ids))

    if duplicates:
        parts = [f"{mt}({', '.join(ids)})" for mt, ids in duplicates.items()]
        raise HTTPException(
            status_code=422,
            detail=_t("default_model_conflict", conflict="; ".join(parts)),
        )


async def invalidate_provider_caches(request: Request) -> None:
    """Clear backend-instance caches and refresh worker rate-limit configuration."""
    from server.services.tasks.generation_context import invalidate_backend_cache

    invalidate_backend_cache()
    worker = getattr(request.app.state, "generation_worker", None)
    if worker:
        await worker.reload_limits()


# ---------------------------------------------------------------------------
# Single-model settings (provider-model Sheets through server/routers/provider_models.py).
# ---------------------------------------------------------------------------

#: Custom-model fields savable by provider-model Sheets.
CUSTOM_MODEL_SETTING_FIELDS = frozenset(
    {
        "model_id",
        "endpoint",
        "is_default",
        "is_enabled",
        "resolution",
        "capability_overrides",
        "path_overrides",
        "multiplier",
        "entry_binding",
    }
)


def _row_to_input(row: CustomProviderModel, changes: Mapping[str, object] | None = None) -> ModelInput:
    data: dict[str, object] = {
        "model_id": row.model_id,
        "display_name": row.display_name,
        "endpoint": row.endpoint,
        "is_default": row.is_default,
        "is_enabled": row.is_enabled,
        "resolution": row.resolution,
        "capability_overrides": row.capability_overrides,
        "path_overrides": row.path_overrides,
        "multiplier": row.multiplier,
        "entry_binding": row.entry_binding,
    }
    data.update(changes or {})
    return ModelInput.model_validate(data)


def _default_group_conflicts(target: ModelInput, other: ModelInput, specs: Mapping[str, EndpointSpec]) -> bool:
    """Whether two defaults share a default group, using :func:`_check_unique_defaults` rules."""
    target_spec, other_spec = specs[target.endpoint], specs[other.endpoint]
    if target_spec.media_type != other_spec.media_type:
        return False
    if target_spec.media_type != "image":
        return True
    target_caps = target_spec.image_capabilities or frozenset()
    other_caps = other_spec.image_capabilities or frozenset()
    return not target_caps or not other_caps or bool(target_caps & other_caps)


async def update_custom_model(
    session: AsyncSession,
    provider_id: int,
    model_id: str,
    changes: Mapping[str, object],
    _t: Callable[..., str],
) -> CustomProviderModel:
    """Save one custom model's provider settings without committing; return the saved row.

    ``changes`` includes only changed fields (:data:`CUSTOM_MODEL_SETTING_FIELDS`), validated like whole-table
    saves. Disable clears default; making default enables it and clears other defaults in the same group.
    Model-ID changes rewrite global references; callers rewrite project references after commit with
    :func:`rename_project_model_refs`.

    Validate binding shape only; callers check catalog existence and saved-endpoint media type before
    commit (``server/routers/provider_models.py`` ``_apply_binding``), since entries created in this save
    are not yet in the catalog here."""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    rows = await repo.list_models(provider_id)
    row = next((r for r in rows if r.model_id == model_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
    patch = dict(changes)
    if isinstance(patch.get("model_id"), str):
        patch["model_id"] = str(patch["model_id"]).strip()
    if patch.get("is_enabled") is False:
        patch["is_default"] = False
    elif patch.get("is_default") is True:
        patch["is_enabled"] = True
    try:
        target = _row_to_input(row, patch)
    except ValidationError as exc:
        # Request models already validated multipliers/etc.; only endpoint/binding shape may still reject here.
        raise _model_input_rejected(exc, patch.get("endpoint"), patch.get("entry_binding"), _t) from exc
    if not target.model_id:
        raise HTTPException(status_code=422, detail=_t("model_id_required"))
    if target.model_id != row.model_id and has_dot_segment(target.model_id):
        raise HTTPException(status_code=422, detail=_t("model_id_dot_segment", model=target.model_id))
    if target.model_id != row.model_id and row.display_name in ("", row.model_id):
        target = target.model_copy(update={"display_name": target.model_id})
    others = [r for r in rows if r is not row]
    if any(r.model_id == target.model_id for r in others):
        raise HTTPException(status_code=422, detail=_t("duplicate_model_id", model_id=target.model_id))

    specs = await _resolve_model_endpoint_specs(session, [target], _t)
    target = _drop_rebound_path_overrides(target, row)
    _check_protocol_constraints([target], provider.discovery_format, specs, _t)
    _check_model_capability_overrides([target], _t, specs)
    _check_model_path_overrides([target], specs, _t)
    if target.is_default:
        default_rows = [r for r in others if r.is_default]
        default_specs = await _read_endpoint_specs(session, default_rows)
        for other in default_rows:
            other_spec = default_specs.get(other.endpoint)
            if other_spec is None:
                continue
            if _default_group_conflicts(
                target, _row_to_input(other), {target.endpoint: specs[target.endpoint], other.endpoint: other_spec}
            ):
                other.is_default = False

    old_model_id = row.model_id
    for key, value in target.to_db_dict(specs[target.endpoint]).items():
        setattr(row, key, value)
    await session.flush()
    if target.model_id != old_model_id:
        await _rename_global_provider_refs(session, provider_id, old_model_id, target.model_id)
    return row


def _model_input_rejected(
    exc: ValidationError, endpoint: object, entry_binding: object, _t: Callable[..., str]
) -> HTTPException:
    """422 for invalid single-model endpoint/binding shape, identifying which input was rejected."""
    if any(error["loc"][:1] == ("entry_binding",) for error in exc.errors()):
        return HTTPException(status_code=422, detail=_t("provider_model_binding_not_found", binding=entry_binding))
    return HTTPException(status_code=422, detail=_t("unknown_endpoint", endpoint=endpoint))


async def remove_custom_model(session: AsyncSession, provider_id: int, model_id: str, _t: Callable[..., str]) -> None:
    """Remove one custom model and clear global references, without committing.

    Callers clear project references after commit with :func:`cleanup_project_model_refs`."""
    repo = CustomProviderRepository(session)
    row = await repo.get_model_by_ids(provider_id, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
    await repo.delete_model(row.id)
    await _clear_global_provider_refs(session, provider_id, {model_id})


async def _comfyui_default_endpoint(session: AsyncSession, media_type: str | None) -> str | None:
    """Default for new ComfyUI models: prefer same-media ComfyUI endpoints, otherwise the first ComfyUI endpoint."""
    specs: list[EndpointSpec] = []
    for row in await CustomEndpointRepository(session).list_all():
        try:
            spec = endpoint_spec_from_row(row)
        except ValueError:
            continue
        if spec.kind == COMFYUI_KIND:
            specs.append(spec)
    preferred = next((spec for spec in specs if spec.media_type == media_type), None)
    chosen = preferred or (specs[0] if specs else None)
    return chosen.key if chosen is not None else None


async def add_custom_model(
    session: AsyncSession,
    provider_id: int,
    model_id: str,
    *,
    endpoint: str | None,
    media_type: str | None,
    entry_binding: str | None,
    _t: Callable[..., str],
) -> CustomProviderModel:
    """Add a custom model without committing; return its new row.

    Omitted endpoints follow docs/adr/0105: entry bindings/name matches select within entry media type
    (multimodal entries use ``media_type``). Otherwise supplied ``media_type`` selects that type, or infer
    both by name. ComfyUI providers use imported ComfyUI endpoints. New models become defaults if
    that media type has none. Validate as whole-table saves."""
    from lib.custom_provider.discovery import match_discovered_model

    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    model_id = model_id.strip()
    if not model_id:
        raise HTTPException(status_code=422, detail=_t("builtin_model_id_required"))
    if has_dot_segment(model_id):
        raise HTTPException(status_code=422, detail=_t("model_id_dot_segment", model=model_id))
    rows = await repo.list_models(provider_id)
    if any(r.model_id == model_id for r in rows):
        raise HTTPException(status_code=422, detail=_t("builtin_model_duplicate", model=model_id))

    bound = find_bound_entry(entry_binding, media_type) if entry_binding and media_type else None
    if endpoint is None:
        if is_comfyui_protocol(provider.discovery_format):
            endpoint = await _comfyui_default_endpoint(session, bound.media_type if bound else media_type)
            if endpoint is None:
                raise HTTPException(status_code=422, detail=_t("comfyui_model_endpoint_missing"))
        elif bound is not None:
            endpoint = infer_endpoint(model_id, provider.discovery_format, bound.media_type)
        else:
            entry, by_name = match_discovered_model(model_id, provider.discovery_format, media_type)
            endpoint = (
                infer_endpoint(model_id, provider.discovery_format, media_type)
                if entry is None and media_type is not None
                else by_name
            )
    try:
        target = ModelInput(
            model_id=model_id, display_name=model_id, endpoint=endpoint, is_enabled=True, entry_binding=entry_binding
        )
    except ValidationError as exc:
        raise _model_input_rejected(exc, endpoint, entry_binding, _t) from exc

    specs = await _resolve_model_endpoint_specs(session, [target], _t)
    _check_protocol_constraints([target], provider.discovery_format, specs, _t)
    # Bound entries must exist in the catalog and match endpoint media types; mismatches are ignored in reads/billing.
    if entry_binding and find_bound_entry(entry_binding, specs[target.endpoint].media_type) is None:
        raise HTTPException(status_code=422, detail=_t("provider_model_binding_not_found", binding=entry_binding))
    default_rows = [r for r in rows if r.is_default]
    default_specs = await _read_endpoint_specs(session, default_rows)
    taken = any(
        other.endpoint in default_specs
        and _default_group_conflicts(
            target,
            _row_to_input(other),
            {target.endpoint: specs[target.endpoint], other.endpoint: default_specs[other.endpoint]},
        )
        for other in default_rows
    )
    if not taken:
        target = target.model_copy(update={"is_default": True})
    row = CustomProviderModel(provider_id=provider_id, **target.to_db_dict(specs[target.endpoint]))
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True)
class CustomModelSettings:
    """Custom-model Sheet settings: endpoint, request URLs, default resolution, and supported provider parameters."""

    endpoint: str
    resolution: str | None
    path_overrides: dict[str, str] | None
    requests: list[EndpointRequestDescriptor]
    capabilities: CapabilityView | None


async def custom_model_settings(session: AsyncSession, row: CustomProviderModel) -> CustomModelSettings:
    """Read custom-model Sheet settings; unresolved endpoints leave request-address/parameter views empty."""
    specs = await _read_endpoint_specs(session, [row])
    spec = specs.get(row.endpoint)
    if spec is None:
        return CustomModelSettings(row.endpoint, row.resolution, None, [], None)
    return CustomModelSettings(
        endpoint=row.endpoint,
        resolution=row.resolution,
        path_overrides=effective_path_overrides(row.path_overrides, spec.requests) or None,
        requests=[EndpointRequestDescriptor(**asdict(r)) for r in spec.requests],
        capabilities=custom_capability_view(
            model_id=row.model_id,
            endpoint=row.endpoint,
            spec=spec,
            raw_overrides=row.capability_overrides,
            applied_overrides=_effective_overrides_for_response(
                row.endpoint, row.model_id, row.capability_overrides, spec, row.entry_binding
            ),
            entry_binding=row.entry_binding,
        ),
    )


# ---------------------------------------------------------------------------
# Provider CRUD
# ---------------------------------------------------------------------------


@router.get("")
async def list_providers(
    session: AsyncSession = Depends(get_async_session),
):
    """List all custom providers including model lists."""
    repo = CustomProviderRepository(session)
    pairs = await repo.list_providers_with_models()
    endpoint_specs = await _read_endpoint_specs(session, [model for _, models in pairs for model in models])
    return {
        "providers": [
            _provider_to_response(
                p,
                models,
                endpoint_specs,
            )
            for p, models in pairs
        ]
    }


# Register /endpoints before /{provider_id}, or FastAPI interprets "endpoints" as provider_id.
@router.get("/endpoints", response_model=EndpointCatalogResponse)
async def list_endpoint_catalog(
    session: AsyncSession = Depends(get_async_session),
) -> EndpointCatalogResponse:
    """Expose both endpoint namespaces as frontend source of truth for dropdowns, paths, and grouping.

    Built-ins use ENDPOINT_REGISTRY; custom definitions resolve from custom_endpoint rows on demand,
    without startup-wide loading, so in-place edits appear next fetch. Skip/warn on unconstructable specs
    (manual DB edits only) rather than failing catalogs: one bad definition must not empty entire selectors."""
    specs = list(ENDPOINT_REGISTRY.values())
    for row in await CustomEndpointRepository(session).list_all():
        try:
            specs.append(endpoint_spec_from_row(row))
        except (KeyError, TypeError, ValueError):
            logger.warning("自定义调用端点定义无法构造 spec，已跳过: id=%s", row.id, exc_info=True)
    return EndpointCatalogResponse(
        endpoints=[EndpointDescriptor(**endpoint_spec_to_dict(spec)) for spec in specs],
    )


@router.get("/endpoints/{endpoint_key}/definition")
async def get_endpoint_definition(endpoint_key: str, _t: Translator) -> dict[str, Any]:
    """Get built-in declarative definition JSON for verbatim POST as a ce-<id> personal copy.

    Python built-ins have no definition and return 404 like unknown keys. Return raw definitions without
    envelopes, identical to import/export file formats."""
    spec = ENDPOINT_REGISTRY.get(endpoint_key)
    if spec is None or spec.definition is None:
        raise HTTPException(status_code=404, detail=_t("endpoint_definition_not_found", endpoint=endpoint_key))
    return dict(spec.definition)


@router.post("", status_code=201)
async def create_provider(
    body: CreateProviderRequest,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """Create a custom provider, optionally with models."""
    specs: dict[str, EndpointSpec] = {}
    if body.models:
        _check_duplicate_model_ids(body.models, _t)
        specs = await _resolve_model_endpoint_specs(session, body.models, _t)
        _check_unique_defaults(body.models, specs, _t)
        _check_protocol_constraints(body.models, body.discovery_format, specs, _t)
        _check_model_capability_overrides(body.models, _t, specs)
        _check_model_path_overrides(body.models, specs, _t)
    repo = CustomProviderRepository(session)
    model_dicts = [m.to_db_dict(specs[m.endpoint]) for m in body.models] if body.models else None
    provider = await repo.create_provider(
        display_name=body.display_name,
        discovery_format=body.discovery_format,
        base_url=body.base_url,
        api_key=body.api_key,
        models=model_dicts,
        image_max_workers=body.image_max_workers,
        video_max_workers=body.video_max_workers,
        audio_max_workers=body.audio_max_workers,
    )
    await session.commit()
    await invalidate_provider_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider.id)
    return _provider_to_response(
        provider,
        models,
        await _read_endpoint_specs(session, models),
    )


@router.get("/{provider_id}")
async def get_provider(
    provider_id: int,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """Get one custom provider's details."""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    models = await repo.list_models(provider_id)
    return _provider_to_response(
        provider,
        models,
        await _read_endpoint_specs(session, models),
    )


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: int,
    body: UpdateProviderRequest,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """Update custom-provider configuration."""
    repo = CustomProviderRepository(session)
    kwargs = {}
    if body.display_name is not None:
        kwargs["display_name"] = body.display_name
    if body.base_url is not None:
        kwargs["base_url"] = body.base_url
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key

    if not kwargs:
        raise HTTPException(status_code=400, detail=_t("at_least_one_field_required"))

    provider = await repo.update_provider(provider_id, **kwargs)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))

    await session.commit()
    await invalidate_provider_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider_id)
    return _provider_to_response(
        provider,
        models,
        await _read_endpoint_specs(session, models),
    )


@router.put("/{provider_id}")
async def full_update_provider(
    provider_id: int,
    body: FullUpdateProviderRequest,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """Atomically update provider metadata/model list in one transaction and clear global defaults referencing removed models.

    Omitted ``models`` updates only metadata/concurrency, leaving models unchanged."""
    repo = CustomProviderRepository(session)
    # Protocol is immutable after creation; read the stored row, not requests, before writes. Check protocol
    # before overrides so comfyui gets dedicated unsupported-overrides wording rather than generic
    # endpoint-no-end-frame refusals.
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    stored_models = {m.model_id: m for m in await repo.list_models(provider_id)}
    models: list[ModelInput] | None = None
    removed_model_ids: set[str] = set()
    specs: dict[str, EndpointSpec] = {}
    if body.models is not None:
        _check_duplicate_model_ids(body.models, _t)
        specs = await _resolve_model_endpoint_specs(session, body.models, _t)
        _check_unique_defaults(body.models, specs, _t)
        models = [_drop_rebound_path_overrides(m, stored_models.get(m.model_id)) for m in body.models]
        _check_protocol_constraints(models, provider.discovery_format, specs, _t)
        _check_model_capability_overrides(models, _t, specs)
        _check_model_path_overrides(models, specs, _t)
    kwargs: dict = {
        "display_name": body.display_name,
        "base_url": body.base_url,
        # PUT owns concurrency caps: always write, including None clears, without nonempty-only updates.
        "image_max_workers": body.image_max_workers,
        "video_max_workers": body.video_max_workers,
        "audio_max_workers": body.audio_max_workers,
    }
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key
    provider = await repo.update_provider(provider_id, **kwargs)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    if models is not None:
        await repo.replace_models(provider_id, [m.to_db_dict(specs[m.endpoint]) for m in models])
        removed_model_ids = stored_models.keys() - {m.model_id for m in models}
        await _clear_global_provider_refs(session, provider_id, removed_model_ids)
    await session.commit()
    await invalidate_provider_caches(request)
    await cleanup_project_model_refs(provider_id, removed_model_ids)
    await session.refresh(provider)
    saved_models = await repo.list_models(provider_id)
    return _provider_to_response(
        provider,
        saved_models,
        await _read_endpoint_specs(session, saved_models),
    )


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: int,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a custom provider, cascading model deletion and clearing dangling defaults."""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    prefix = f"{make_provider_id(provider_id)}/"
    await repo.delete_provider(provider_id)
    await _clear_global_provider_refs(session, provider_id)
    await session.commit()
    await invalidate_provider_caches(request)
    # Clean project-level provider references in a thread pool for synchronous file I/O, avoiding event-loop blocking.
    await run_sync_transaction(_cleanup_project_refs, prefix, _PROJECT_BACKEND_KEYS)


# ---------------------------------------------------------------------------
# Stateless operations.
# ---------------------------------------------------------------------------


@router.post("/discover")
async def discover_models_endpoint(
    body: ConnectivityCheckRequest,
    _t: Translator,
):
    """Discover models using discovery_format + base_url + api_key."""
    return await _run_discover(body.discovery_format, body.base_url, body.api_key, _t)


@router.post("/discover-anthropic", response_model=DiscoverResponse)
async def discover_anthropic_models_endpoint(
    body: DiscoverAnthropicRequest,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """Anthropic model discovery for Agent configuration.

    Missing credentials fall back to active credentials (AgentCredentialRepository)."""
    body_key = (body.api_key or "").strip()
    needs_key = not body_key
    needs_url = body.base_url is None

    cred = None
    if needs_key or needs_url:
        from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

        cred = await AgentCredentialRepository(session).get_active()

    api_key = body_key if not needs_key else (cred.api_key if cred else "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail=_t("anthropic_discovery_no_key"))

    base_url = body.base_url if not needs_url else (_credential_discovery_base(cred) if cred else None)
    if base_url:
        from lib.config.url_utils import InvalidAnthropicBaseUrlError, validate_anthropic_base_url

        try:
            base_url = validate_anthropic_base_url(base_url)
        except InvalidAnthropicBaseUrlError as exc:
            # Fixed wording, never echo inputs: URLs may contain mistakenly entered api_key.
            raise HTTPException(status_code=422, detail=_t("agent_base_url_invalid")) from exc

    return await _run_discover("anthropic", base_url, api_key, _t)


@router.post("/{provider_id}/discover")
async def discover_models_by_id(
    provider_id: int,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """Discover a provider's available models using stored credentials."""
    return await discover_custom_provider_models(session, provider_id, _t)


async def discover_custom_provider_models(
    session: AsyncSession, provider_id: int, _t: Callable[..., str]
) -> DiscoverResponse:
    """Discover custom-provider models with stored credentials, including matched entries/default endpoints for each."""
    provider = await CustomProviderRepository(session).get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return await _run_discover(provider.discovery_format, provider.base_url, provider.api_key, _t)


@router.post("/test")
async def check_connectivity(
    body: ConnectivityCheckRequest,
    _t: Translator,
):
    """Check discovery_format + base_url + api_key reachability. Free probe, proving no model generation capability."""
    return await _run_connectivity_check(body.discovery_format, body.base_url, body.api_key, _t)


@router.post("/{provider_id}/test")
async def check_connectivity_by_id(
    provider_id: int, _t: Translator, session: AsyncSession = Depends(get_async_session)
):
    """Check a provider's connectivity using stored credentials."""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return await _run_connectivity_check(provider.discovery_format, provider.base_url, provider.api_key, _t)


def _credential_discovery_base(cred: Any) -> str | None:
    """Active-credential root for model discovery.

    If preset credentials did not override base_url (stored value still equals preset ``messages_url``),
    use preset ``discovery_url`` (DeepSeek model lists are outside the messages root). Custom/overridden
    credentials use stored values. Matches frontend rules that prefills do not count as overrides."""
    from lib.agent.agent_provider_catalog import get_preset

    preset = get_preset(cred.preset_id) if cred.preset_id else None
    if preset is not None and cred.base_url == preset.messages_url:
        return preset.discovery_url or preset.messages_url
    return cred.base_url


async def _run_discover(
    discovery_format: str,
    base_url: str | None,
    api_key: str,
    _t: Callable[..., str],
    *,
    discover_models_fn: Callable[..., Awaitable[list[dict]]] | None = None,
) -> DiscoverResponse:
    """Shared discovery logic for plaintext/stored credential entry points."""
    from lib.config.url_utils import InvalidAnthropicBaseUrlError
    from lib.custom_provider.discovery import UnsupportedDiscoveryFormatError, discover_models

    if is_comfyui_protocol(discovery_format):
        # Structured not-applicable, not 422: the protocol lacks discovery, rather than discovery failing.
        return DiscoverResponse(not_applicable=True, reason=_t("discovery_not_applicable_comfyui"))
    try:
        discover = discover_models_fn or discover_models
        models = await discover(
            discovery_format=discovery_format,
            base_url=base_url or None,
            api_key=api_key,
        )
        # IDs with dot segments are rejected on add, so exclude from addable lists.
        return DiscoverResponse(models=[m for m in models if not has_dot_segment(str(m.get("model_id", "")))])
    except UnsupportedDiscoveryFormatError as exc:
        raise BadRequestError("invalid_discovery_format", discovery_format=discovery_format) from exc
    except InvalidAnthropicBaseUrlError as exc:
        # Boundary-rejected addresses are not upstream failures: return 422 like /discover-anthropic, without echoing inputs.
        raise HTTPException(status_code=422, detail=_t("agent_base_url_invalid")) from exc
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("模型发现失败: %s", err_msg)
        raise HTTPException(status_code=502, detail=_t("discovery_failed", err_msg=err_msg)) from exc


async def _run_connectivity_check(
    discovery_format: str,
    base_url: str,
    api_key: str,
    _t: Callable[..., str],
    *,
    openai_probe: Callable[[str, str, Callable[..., str]], ConnectivityCheckResponse] | None = None,
    google_probe: Callable[[str, str, Callable[..., str]], ConnectivityCheckResponse] | None = None,
) -> ConnectivityCheckResponse:
    """Shared connectivity checks for plaintext/stored credential entry points."""
    try:
        if discovery_format == "openai":
            result = await asyncio.wait_for(
                asyncio.to_thread(openai_probe or _check_openai, base_url, api_key, _t),
                timeout=_CONNECTIVITY_CHECK_TIMEOUT,
            )
        elif discovery_format == "google":
            result = await asyncio.wait_for(
                asyncio.to_thread(google_probe or _check_google, base_url, api_key, _t),
                timeout=_CONNECTIVITY_CHECK_TIMEOUT,
            )
        elif is_comfyui_protocol(discovery_format):
            # No to_thread: ComfyUI probes use async httpx directly; other checks use synchronous SDKs.
            result = await asyncio.wait_for(
                _check_comfyui(base_url, api_key, _t),
                timeout=_CONNECTIVITY_CHECK_TIMEOUT,
            )
        else:
            return ConnectivityCheckResponse(
                success=False,
                message=_t("connectivity_check_unsupported_format", discovery_format=discovery_format),
            )
        return result
    except TimeoutError:
        return ConnectivityCheckResponse(
            success=False,
            message=_t("connectivity_check_timeout"),
        )
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("连通性检查失败 [%s]: %s", discovery_format, err_msg)
        return ConnectivityCheckResponse(
            success=False,
            message=_t("connectivity_check_failed", err_msg=err_msg),
        )


def _check_openai(
    base_url: str,
    api_key: str,
    _t: Callable[..., str],
    *,
    client_factory: Callable[..., Any] | None = None,
) -> ConnectivityCheckResponse:
    """Check OpenAI-compatible APIs using models.list()."""
    from openai import OpenAI

    from lib.config.url_utils import ensure_openai_base_url

    client = (client_factory or OpenAI)(api_key=api_key, base_url=ensure_openai_base_url(base_url))
    models = client.models.list()
    count = sum(1 for _ in models)
    return ConnectivityCheckResponse(
        success=True,
        message=_t("connectivity_check_ok"),
        model_count=count,
    )


def _check_google(
    base_url: str,
    api_key: str,
    _t: Callable[..., str],
    *,
    client_factory: Callable[..., Any] | None = None,
) -> ConnectivityCheckResponse:
    """Check Google genai APIs using models.list()."""
    from google import genai

    from lib.config.url_utils import ensure_google_base_url

    effective_url = ensure_google_base_url(base_url)
    http_options: genai.types.HttpOptionsDict | None = {"base_url": effective_url} if effective_url else None
    client = (client_factory or genai.Client)(api_key=api_key, http_options=http_options)
    pager = client.models.list()
    count = sum(1 for _ in pager)
    return ConnectivityCheckResponse(
        success=True,
        message=_t("connectivity_check_ok"),
        model_count=count,
    )


def _comfyui_probe_headers(api_key: str) -> dict[str, str]:
    """ComfyUI probe headers: send ``api_key`` directly as Bearer, or no credentials when empty.

    Do not render definition ``auth`` here (``docs/adr/0081``): endpoint-specific templates are unavailable
    to provider-level probes, which must not guess for one endpoint. Custom-header reverse-proxy auth
    may therefore fail probing; frontend directs users to connection-test results as authoritative."""
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


async def _check_comfyui(
    base_url: str,
    api_key: str,
    _t: Callable[..., str],
) -> ConnectivityCheckResponse:
    """Check ComfyUI reachability with ``GET {base_url}/system_stats`` and echo ``comfyui_version``.

    Omit ``model_count``: ComfyUI has no enumerable list, and 0 would imply no models.

    Validate destinations through ``artifact_http_client``, sharing submission/polling/download gates:
    reject link-local/cloud-metadata addresses, allow loopback/private networks for legitimate self-hosted
    ComfyUI. Rejections follow ``_run_connectivity_check`` failure responses, shaped like upstream unreachability."""
    url = base_url.strip().rstrip("/") + _COMFYUI_SYSTEM_STATS_PATH
    async with artifact_http_client(timeout=_CONNECTIVITY_CHECK_TIMEOUT) as client:
        resp = await client.get(url, headers=_comfyui_probe_headers(api_key))
    raise_for_status_redacted(resp)
    payload = resp.json()
    system = payload.get("system") if isinstance(payload, dict) else None
    version = system.get("comfyui_version") if isinstance(system, dict) else None
    # Missing versions still mean reachable: old versions/proxies omit the field, and rejection would block usable deployments.
    message = (
        _t("connectivity_check_comfyui_ok", version=str(version))
        if version
        else _t("connectivity_check_comfyui_ok_unknown_version")
    )
    return ConnectivityCheckResponse(success=True, message=message)
