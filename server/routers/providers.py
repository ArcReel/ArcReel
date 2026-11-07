"""Provider configuration management API.

Endpoints for provider listing, individual configuration reads/writes, and connectivity checks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_market_core.video_backend_contract import VideoAudioMode
from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
from lib.backends.gemini_shared import VERTEX_SCOPES
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.billing.multipliers import (
    PROVIDER_MULTIPLIER_KEY,
    get_default_provider_multiplier,
    get_provider_multiplier,
    set_provider_multiplier,
)
from lib.catalog import query as catalog
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import mask_secret
from lib.config.resolver import (
    ConfigResolver,
    VideoBucketCapabilityError,
    VoiceConsistency,
    builtin_video_audio_track,
    derive_voice_consistency,
)
from lib.config.service import ConfigService, ProviderConfigValueError
from lib.config.url_utils import normalize_base_url
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.db import async_session_factory, get_async_session
from lib.db.base import dt_to_iso
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.video_request_facts import ResolutionOverride, VideoRequestFactsError
from lib.i18n import render_message, translate_or
from lib.infra.api_errors import BadRequestError, UnprocessableError
from lib.infra.async_thread import run_sync_transaction
from lib.infra.data_root_layout import DataRootLayout
from server.dependencies import get_config_service
from server.i18n import Locale, Translator, load_display_names_or_builtin
from server.routers._validators import split_video_backend_query
from server.services.tasks.video_caps import capability_request_facts, duration_constraints_payload

if TYPE_CHECKING:
    from lib.db.models.credential import ProviderCredential

logger = logging.getLogger(__name__)

MAX_VERTEX_CREDENTIALS_BYTES = 1024 * 1024  # 1 MiB

router = APIRouter(prefix="/providers", tags=["Providers"])

_CREDENTIAL_KEYS = frozenset({"api_key", "credentials_path", "base_url", "access_key", "secret_key"})

# ---------------------------------------------------------------------------
# Field metadata mapping (key → label/type/placeholder).
# ---------------------------------------------------------------------------

_FIELD_META: dict[str, dict[str, str]] = {
    "api_key": {"label": "API Key", "type": "secret"},
    "access_key": {"label": "Access Key", "type": "secret"},
    "secret_key": {"label": "Secret Key", "type": "secret"},
    "base_url": {"label": "Base URL", "type": "url", "placeholder": "Default"},
    "wan3_base_url": {"label": "Wan 3.0 Base URL", "type": "url", "placeholder": "Default"},
    "credentials_path": {"label": "Vertex Credentials Path", "type": "text"},
    "gcs_bucket": {"label": "GCS Bucket", "type": "text"},
    "image_rpm": {"label": "Image RPM", "type": "number"},
    "video_rpm": {"label": "Video RPM", "type": "number"},
    "request_gap": {"label": "Request Gap (sec)", "type": "number"},
    "image_max_workers": {"label": "Image Max Workers", "type": "number"},
    "video_max_workers": {"label": "Video Max Workers", "type": "number"},
    "audio_max_workers": {"label": "Audio Max Workers", "type": "number"},
}


# ---------------------------------------------------------------------------
# Pydantic models.
# ---------------------------------------------------------------------------


class ModelInfoResponse(BaseModel):
    display_name: str
    media_type: str
    capabilities: list[str]
    default: bool
    # Duration coupling (resolution↔duration, references↔duration) is absent from catalogs: contextual narrowing comes from
    # /providers/video-capabilities and /projects/{name}/video-capabilities for the
    # frontend, with rules centralized in lib.config.resolver.
    supported_durations: list[int] = []
    resolutions: list[str] = []
    # Output audio-track shape (``VideoAudioMode``: controllable / always audible / always silent) per execution path: one
    # model can differ between image-to-video and reference-to-video subpaths (Kling v3-omni's multi-image subject path lacks
    # an audio switch). Settings select by effective bucket to enable switches; capability summaries treat every non-always-silent
    # mode as having audio. Frontend does not interpret capability tokens or synthesize the three-state value.
    # Backend VideoCapabilities is authoritative (lib.config.resolver.builtin_video_audio_track);
    # nonvideo models always use always_off.
    audio_track: str = VideoAudioMode.ALWAYS_OFF.value
    reference_route_audio_track: str = VideoAudioMode.ALWAYS_OFF.value
    # Voice-consistency tier without project context (global model selection): generation_mode is unknown, so derive via non-reference
    # video paths, always downgrading native. lib.config.resolver.derive_voice_consistency is the sole
    # formula; frontend reads only enums. With project context use /projects/{name}/video-capabilities (same function,
    # actual generation_mode) instead; this field does not participate.
    voice_consistency: VoiceConsistency = "none"


class ProviderSummary(BaseModel):
    id: str
    display_name: str
    description: str
    status: str
    media_types: list[str]
    capabilities: list[str]
    # Number of credentials (UI calls them keys); 0 when absent.
    credential_count: int
    models: dict[str, ModelInfoResponse]


class ProvidersListResponse(BaseModel):
    providers: list[ProviderSummary]


class FieldInfo(BaseModel):
    key: str
    label: str
    type: str
    required: bool
    is_set: bool
    value: str | None = None
    value_masked: str | None = None
    placeholder: str | None = None


class CredentialSecretField(BaseModel):
    """Secret inputs required by credential forms (derived from provider required ∩ secret ∩ credential keys).

    Drive settings forms: single-secret providers expose ``[api_key]`` (ADR 0037), while Kling exposes
    ``[api_key, access_key, secret_key]`` (alternative groups in ``secret_field_groups``).
    Key names remain identical throughout frontend reads/writes."""

    key: str
    label: str


class ProviderConfigResponse(BaseModel):
    id: str
    display_name: str
    description: str
    status: str
    media_types: list[str]
    fields: list[FieldInfo]
    # Whether provider credentials accept custom base_url (source: optional_keys includes base_url).
    # base_url belongs to credentials, not fields; frontend uses this to show URL inputs in key forms.
    supports_base_url: bool
    # Ordered credential secret fields (source: registry required_keys ∩ secret_keys ∩ credential keys).
    secret_fields: list[CredentialSecretField]
    # Alternative credential groups: frontend requires all fields in at least one group, not all fields overall.
    # Source: registry credential_groups; empty declarations fall back here to [all secret_fields],
    # preserving pre-migration all-required semantics.
    secret_field_groups: list[list[str]]


class ConnectivityCheckResponse(BaseModel):
    success: bool
    available_models: list[str]
    message: str


class CredentialResponse(BaseModel):
    id: int
    provider: str
    name: str
    api_key_masked: str | None = None
    credentials_filename: str | None = None
    base_url: str | None = None
    # Redact fields independently, not as one combined secret; always None except for Kling (ADR 0037).
    access_key_masked: str | None = None
    secret_key_masked: str | None = None
    is_active: bool
    created_at: str


class CredentialListResponse(BaseModel):
    credentials: list[CredentialResponse]


def _stripped(v: str | None) -> str | None:
    """Trim surrounding whitespace from credential string inputs.

    Pasted keys often carry stray leading/trailing whitespace or newlines that
    silently break auth; normalizing at the API boundary covers the frontend and
    any direct/third-party caller. Unset fields keep their None default (the
    validator runs only on provided values), so PATCH preserve-semantics — an
    omitted secret leaves the stored value untouched — are unaffected.
    """
    return v.strip() if isinstance(v, str) else v


_StrippedStr = Annotated[str, AfterValidator(_stripped)]
_StrippedOptStr = Annotated[str | None, AfterValidator(_stripped)]


class ProviderMultiplierResponse(BaseModel):
    """Provider multiplier: ``multiplier`` is effective; ``default`` applies when unset."""

    multiplier: float
    default: float


class ProviderMultiplierUpdate(BaseModel):
    """Set provider multiplier; ``null`` restores the default."""

    multiplier: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None


class CreateCredentialRequest(BaseModel):
    name: _StrippedStr
    api_key: _StrippedOptStr = None
    base_url: _StrippedOptStr = None
    access_key: _StrippedOptStr = None
    secret_key: _StrippedOptStr = None


class UpdateCredentialRequest(BaseModel):
    name: _StrippedOptStr = None
    api_key: _StrippedOptStr = None
    base_url: _StrippedOptStr = None
    access_key: _StrippedOptStr = None
    secret_key: _StrippedOptStr = None


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _validate_provider(provider_id: str, _t: Callable[..., str]) -> None:
    """Validate provider ID existence, raising 404 if absent."""
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=_t("unknown_provider", value=provider_id))


def _submitted_secret_values(
    provider_id: str, body: CreateCredentialRequest | UpdateCredentialRequest
) -> dict[str, str | None]:
    """Extract secret fields used for credential-group switching, shared by create/update for consistent field sets.

    Filter dynamically using provider `secret_keys` in `PROVIDER_REGISTRY`, not a hardcoded exclusion list,
    sharing declarations with `credential_groups`. New credential fields still require matching fields in
    `CreateCredentialRequest`/`UpdateCredentialRequest` as well as registry `secret_keys`: `hasattr` silently
    filters keys absent from those request models."""
    meta = PROVIDER_REGISTRY[provider_id]
    return {k: getattr(body, k, None) for k in meta.secret_keys if hasattr(body, k)}


def _resolve_credential_group_switch(
    provider_id: str,
    submitted: Mapping[str, str | None],
    current: Mapping[str, str | None] | None,
    _t: Callable[..., str],
) -> set[str]:
    """Determine whether one submission switches credential groups; return other-group fields to clear.

    ``ProviderMeta.credential_groups`` drives switching:
    - Nonempty submitted fields spanning mutually exclusive groups make intent ambiguous; reject rather than silently discard entered values.
    - Fully supplying a group not previously complete in stored values means switching into it: return all
      fields in other declared groups for clearing, preventing stale-group remnants from silently retaining the old mode.
    - Fully supplying a group already complete in stored values (routine rotation on a legacy coexisting
      row) does not switch or clear another group, preserving dormant credentials the user did not touch.
    - Without fully supplying a group (including most providers without credential_groups), return an
      empty set, preserving partial-update semantics.

    ``current`` supplies original stored values for switch-in detection. Creation passes None: new rows
    have nothing to clear and reuse only cross-group ambiguity rejection."""
    meta = PROVIDER_REGISTRY[provider_id]
    groups = meta.credential_groups
    if not groups:
        return set()
    touched = [g for g in groups if any(submitted.get(k) for k in g)]
    if len(touched) > 1:
        raise HTTPException(status_code=422, detail=_t("credential_group_ambiguous"))
    covered = meta.fully_covered_credential_groups(submitted)
    if not covered:
        return set()
    target = covered[0]
    if current is not None:
        pre_covered = {frozenset(g) for g in meta.fully_covered_credential_groups(current)}
        if frozenset(target) in pre_covered:
            return set()
    target_keys = set(target)
    return {k for group in groups for k in group if k not in target_keys}


async def _get_credential_or_404(
    repo: CredentialRepository,
    provider_id: str,
    cred_id: int,
    _t: Callable[..., str],
) -> ProviderCredential:
    """Get a credential and verify ownership; raise 404 if absent."""
    cred = await repo.get_by_id(cred_id)
    if not cred or cred.provider != provider_id:
        raise HTTPException(status_code=404, detail=_t("credentials_not_found"))
    return cred


def _cred_to_response(cred: ProviderCredential) -> CredentialResponse:
    return CredentialResponse(
        id=cred.id,
        provider=cred.provider,
        name=cred.name,
        api_key_masked=mask_secret(cred.api_key) if cred.api_key else None,
        credentials_filename=Path(credentials_file).name if (credentials_file := cred.credentials_file()) else None,
        base_url=cred.base_url,
        access_key_masked=mask_secret(cred.access_key) if cred.access_key else None,
        secret_key_masked=mask_secret(cred.secret_key) if cred.secret_key else None,
        is_active=cred.is_active,
        created_at=dt_to_iso(cred.created_at) or "",
    )


async def _invalidate_caches(request: Request) -> None:
    from server.services.tasks.generation_context import invalidate_backend_cache

    invalidate_backend_cache()
    worker = getattr(request.app.state, "generation_worker", None)
    if worker:
        await worker.reload_limits()


def _build_field(
    key: str,
    required: bool,
    db_entry: dict[str, Any] | None,
) -> FieldInfo:
    """Build FieldInfo from key, required status, and a DB-loaded entry."""
    meta = _FIELD_META.get(key, {"label": key, "type": "text"})
    is_set = db_entry is not None and db_entry.get("is_set", False)

    field: dict[str, Any] = {
        "key": key,
        "label": meta["label"],
        "type": meta["type"],
        "required": required,
        "is_set": is_set,
    }

    if "placeholder" in meta:
        field["placeholder"] = meta["placeholder"]

    if db_entry is not None and is_set:
        if meta["type"] == "secret":
            field["value_masked"] = db_entry.get("masked", "••••")
        else:
            field["value"] = db_entry.get("value", "")
    else:
        if meta["type"] == "secret":
            field["value_masked"] = None
        else:
            field["value"] = ""

    return FieldInfo(**field)


# ---------------------------------------------------------------------------
# Endpoints.
# ---------------------------------------------------------------------------


def _video_reference_audio_mode(provider_id: str, model_id: str) -> str:
    """Reference-audio transport shape for this video model; "none" for nonvideo providers or undeclared support.

    `VideoCapabilities` is the sole backend declaration source; catalog model information does not copy
    it, as in `lib/config/resolver.py::_resolve_video_caps_for_model`. Routers (outside lib) are not subject
    to lib.config layering contracts and may read backend declarations directly."""
    try:
        caps = builtin_video_capabilities_for_model(provider_id, model_id)
    except ValueError:
        return "none"
    return caps.reference_audio_mode.value


@router.get("", response_model=ProvidersListResponse)
async def list_providers(
    _t: Translator,
    locale: Locale,
    svc: Annotated[ConfigService, Depends(get_config_service)],
) -> ProvidersListResponse:
    """Return every provider and its status."""
    statuses = await svc.get_all_providers_status()
    providers = []
    for s in statuses:
        models: dict[str, ModelInfoResponse] = {}
        for mid, minfo in (s.models or {}).items():
            silent = VideoAudioMode.ALWAYS_OFF.value
            audio_track = builtin_video_audio_track(s.name, mid, generation_type="i2v") or silent
            reference_route_audio_track = builtin_video_audio_track(s.name, mid, generation_type="r2v") or silent
            reference_audio_mode = (
                _video_reference_audio_mode(s.name, mid) if minfo.get("media_type") == "video" else "none"
            )
            models[mid] = ModelInfoResponse(
                **minfo,
                audio_track=audio_track,
                reference_route_audio_track=reference_route_audio_track,
                # generation_mode / character_voice_binding are both None: catalogs lack project context, derive via non-reference
                # video paths, and use i2v audio-track bits too. With project context use
                # /projects/{name}/video-capabilities。
                voice_consistency=derive_voice_consistency(
                    reference_audio_mode=reference_audio_mode,
                    generation_mode=None,
                    has_audio=audio_track != silent,
                    character_voice_binding=None,
                ),
            )
        providers.append(
            ProviderSummary(
                id=s.name,
                # Registry/DB display_name is the source original; render translated names in the request language when available,
                # otherwise return unchanged (brands and user-entered custom-provider names).
                display_name=translate_or(f"provider_name_{s.name}", s.display_name, locale),
                description=_t(f"provider_desc_{s.name}"),
                status=s.status,
                media_types=s.media_types,
                capabilities=s.capabilities,
                credential_count=s.credential_count,
                models=models,
            )
        )
    return ProvidersListResponse(providers=providers)


@router.get("/video-capabilities")
async def get_model_video_capabilities(
    _t: Translator,
    video_backend: Annotated[str, Query()],
    resolution: Annotated[str | None, Query()] = None,
    uses_reference_images: Annotated[bool, Query()] = False,
):
    """Video-model capabilities without project context: creation wizards resolve candidate models before a project exists.

    Share the bucket-capability gate/model-capability resolution chain with `/projects/{name}/video-capabilities`,
    but with no project to read: `default_duration` / `generation_mode` and other preferences are None.
    `duration_constraints` comes from video request facts for the candidate's bucket (`uses_reference_images`
    true → r2v, otherwise i2v). `resolution` evaluates as an override (omitted means automatic: custom
    providers use model defaults, others omit resolution). Bare-provider completion and format validation
    match the project endpoint."""
    provider_id, model_id = split_video_backend_query(video_backend)
    resolver = ConfigResolver(async_session_factory)
    generation_type = "r2v" if uses_reference_images else "i2v"
    candidate_project = {f"video_provider_{generation_type}": f"{provider_id}/{model_id}"}
    try:
        await resolver.resolve_video_backend(candidate_project, None, generation_type=generation_type)
        caps = await resolver.video_capabilities_for_model(provider_id, model_id, None, generation_type=generation_type)
        if (caps["provider_id"], caps["model"]) != (provider_id, model_id):
            raise BadRequestError("video_capability_reference_unavailable", provider=provider_id, model=model_id)
        caps["duration_constraints"] = duration_constraints_payload(
            await capability_request_facts(
                candidate_project,
                generation_type=generation_type,
                config_resolver=resolver,
                resolution_override=ResolutionOverride(resolution or None),
            )
        )
        return caps
    except VideoBucketCapabilityError as exc:
        raise BadRequestError(exc.code, **exc.params) from exc
    except VideoRequestFactsError as exc:
        # Video-request-fact problem codes are errors-catalog keys (ValueError subclass, caught before ValueError).
        raise UnprocessableError(exc.code, **exc.params) from exc
    except ModelNotInCatalogError:
        # Model absent from provider lists: app-level handler returns model-reselection 400 (ValueError subclass, caught before ValueError).
        raise
    except ValueError as exc:
        # Log original exceptions only: str(exc) mixes English technical details, which would introduce untranslated text in en/vi UI if interpolated.
        logger.warning("视频模型 '%s' 能力解析失败: %s", video_backend, exc)
        raise HTTPException(
            status_code=422, detail=_t("video_model_capabilities_unresolved", value=video_backend)
        ) from exc


@router.get("/{provider_id}/config", response_model=ProviderConfigResponse)
async def get_provider_config(
    provider_id: str,
    _t: Translator,
    locale: Locale,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderConfigResponse:
    """Return one provider's configuration fields, merging registry metadata and DB values."""
    _validate_provider(provider_id, _t)

    meta = PROVIDER_REGISTRY[provider_id]
    svc = ConfigService(session)
    db_values = await svc.get_provider_config_masked(provider_id)

    # Derive status from active credentials in the credential table.
    cred_repo = CredentialRepository(session)
    has_active = await cred_repo.has_active_credential(provider_id)
    status = "ready" if has_active else "unconfigured"

    # Build fields: required first, then optional, excluding credentials.
    fields: list[FieldInfo] = []
    fields.extend(
        _build_field(key, required=True, db_entry=db_values.get(key))
        for key in meta.required_keys
        if key not in _CREDENTIAL_KEYS
    )
    fields.extend(
        _build_field(key, required=False, db_entry=db_values.get(key))
        for key in meta.optional_keys
        if key not in _CREDENTIAL_KEYS
    )

    # Credential secret inputs: required ∩ secret ∩ credential keys, preserving required_keys order.
    # Single-secret providers → [api_key] (ADR 0037); Kling → [api_key, access_key, secret_key];
    # alternative groups appear in secret_field_groups below.
    secret_keys = set(meta.secret_keys)
    secret_fields = [
        CredentialSecretField(key=key, label=_FIELD_META.get(key, {"label": key})["label"])
        for key in meta.required_keys
        if key in _CREDENTIAL_KEYS and key in secret_keys
    ]
    # Empty declarations (most single-secret / dual-secret-AND providers) fall back to one required group = all secret_fields,
    # preserving pre-migration all-required semantics. Only Kling declares nonempty credential_groups (api_key versus
    # access_key+secret_key). With empty secret_fields (currently gemini-vertex only, whose file-upload path bypasses
    # group validation), do not synthesize [[]], matching registry.py's empty-group fail-fast semantics
    # and preventing future providers with ordinary forms but no secret fields from getting always-true groups that silently bypass validation.
    secret_field_groups = meta.credential_groups or ([[f.key for f in secret_fields]] if secret_fields else [])

    return ProviderConfigResponse(
        id=provider_id,
        display_name=translate_or(f"provider_name_{provider_id}", meta.display_name, locale),
        description=_t(f"provider_desc_{provider_id}"),
        status=status,
        media_types=catalog.provider_media_types(provider_id),
        fields=fields,
        supports_base_url="base_url" in meta.optional_keys,
        secret_fields=secret_fields,
        secret_field_groups=secret_field_groups,
    )


@router.patch("/{provider_id}/config", status_code=204)
async def patch_provider_config(
    provider_id: str,
    body: dict[str, str | None],
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """Update provider configuration. null deletes a key. Change provider multipliers only through ``PUT /{provider_id}/multiplier``."""
    _validate_provider(provider_id, _t)
    if PROVIDER_MULTIPLIER_KEY in body:
        raise HTTPException(status_code=422, detail=_t("provider_multiplier_not_in_config"))

    svc = ConfigService(session)
    for key, value in body.items():
        if value is None:
            await svc.delete_provider_config(provider_id, key, flush=False)
        else:
            try:
                await svc.set_provider_config(provider_id, key, value, flush=False)
            except ProviderConfigValueError as exc:
                # Replace field names in error wording with the same UI labels (e.g. Image Max Workers) shown in forms.
                params = dict(exc.params)
                meta = _FIELD_META.get(exc.key)
                if meta is not None:
                    params["field"] = meta["label"]
                raise HTTPException(status_code=422, detail=_t(exc.code, **params)) from exc

    await session.commit()

    # Refresh caches and concurrency pools after configuration changes.
    await _invalidate_caches(request)

    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Provider multipliers.
# ---------------------------------------------------------------------------


async def _validate_priced_provider(provider_id: str, session: AsyncSession, _t: Callable[..., str]) -> None:
    """Allow multipliers for built-in and existing custom providers; otherwise raise 404."""
    if is_custom_provider(provider_id):
        try:
            db_id = parse_provider_id(provider_id)
        except ValueError:
            db_id = None
        if db_id is None or await CustomProviderRepository(session).get_provider(db_id) is None:
            raise HTTPException(status_code=404, detail=_t("provider_not_found"))
        return
    _validate_provider(provider_id, _t)


@router.get("/{provider_id}/multiplier", response_model=ProviderMultiplierResponse)
async def get_provider_multiplier_setting(
    provider_id: str,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderMultiplierResponse:
    """Provider multiplier, applied to reference costs of every call through this provider."""
    await _validate_priced_provider(provider_id, session, _t)
    return ProviderMultiplierResponse(
        multiplier=await get_provider_multiplier(session, provider_id),
        default=await get_default_provider_multiplier(session, provider_id),
    )


@router.put("/{provider_id}/multiplier", response_model=ProviderMultiplierResponse)
async def put_provider_multiplier_setting(
    provider_id: str,
    body: ProviderMultiplierUpdate,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderMultiplierResponse:
    """Set provider multiplier for calls settled afterward only; do not recalculate recorded costs."""
    await _validate_priced_provider(provider_id, session, _t)
    await set_provider_multiplier(session, provider_id, body.multiplier)
    await session.commit()
    return ProviderMultiplierResponse(
        multiplier=await get_provider_multiplier(session, provider_id),
        default=await get_default_provider_multiplier(session, provider_id),
    )


# ---------------------------------------------------------------------------
# Credential CRUD endpoints.
# ---------------------------------------------------------------------------


@router.get("/{provider_id}/credentials", response_model=CredentialListResponse)
async def list_credentials(
    provider_id: str,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialListResponse:
    _validate_provider(provider_id, _t)
    repo = CredentialRepository(session)
    creds = await repo.list_by_provider(provider_id)
    return CredentialListResponse(credentials=[_cred_to_response(c) for c in creds])


@router.post("/{provider_id}/credentials", status_code=201, response_model=CredentialResponse)
async def create_credential(
    provider_id: str,
    body: CreateCredentialRequest,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialResponse:
    _validate_provider(provider_id, _t)
    # New rows have nothing to clear; reuse cross-group ambiguity checks only, rejecting contradictory submissions without persistence.
    _resolve_credential_group_switch(provider_id, _submitted_secret_values(provider_id, body), None, _t)
    repo = CredentialRepository(session)
    cred = await repo.create(
        provider=provider_id,
        name=body.name,
        api_key=body.api_key,
        base_url=body.base_url,
        access_key=body.access_key,
        secret_key=body.secret_key,
    )
    await session.commit()
    await _invalidate_caches(request)
    return _cred_to_response(cred)


@router.patch("/{provider_id}/credentials/{cred_id}", status_code=204)
async def update_credential(
    provider_id: str,
    cred_id: int,
    body: UpdateCredentialRequest,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    _validate_provider(provider_id, _t)
    repo = CredentialRepository(session)
    # Establish credential existence first (404 precedes group-ambiguity 422), then use stored originals for switch-in detection.
    cred = await _get_credential_or_404(repo, provider_id, cred_id, _t)
    submitted = _submitted_secret_values(provider_id, body)
    clear_keys = _resolve_credential_group_switch(
        provider_id,
        submitted,
        {k: getattr(cred, k, None) for k in submitted},
        _t,
    )
    kwargs: dict = {}
    if body.name is not None:
        kwargs["name"] = body.name
    if "api_key" in body.model_fields_set:
        kwargs["api_key"] = body.api_key
    if "base_url" in body.model_fields_set:
        kwargs["base_url"] = body.base_url
    if "access_key" in body.model_fields_set:
        kwargs["access_key"] = body.access_key
    if "secret_key" in body.model_fields_set:
        kwargs["secret_key"] = body.secret_key
    # Switching in clears other declared groups to prevent silently retaining the old mode. Switch detection excludes groups touched in this submission,
    # so it never overwrites values entered in this submission.
    for key in clear_keys:
        kwargs[key] = None
    if kwargs:
        await repo.update(cred_id, **kwargs)
        await session.commit()
        if cred.is_active:
            await _invalidate_caches(request)
    return Response(status_code=204)


@router.delete("/{provider_id}/credentials/{cred_id}", status_code=204)
async def delete_credential(
    provider_id: str,
    cred_id: int,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    _validate_provider(provider_id, _t)
    repo = CredentialRepository(session)
    cred = await _get_credential_or_404(repo, provider_id, cred_id, _t)
    cred_path = cred.credentials_file()  # Save before delete, avoiding access to expired ORM objects.
    await repo.delete(cred_id)
    await session.commit()
    await _invalidate_caches(request)
    # Delete associated credential files (e.g. JSON under vertex_keys/) after commit for data consistency.
    if cred_path:
        cred_file = Path(cred_path)
        if cred_file.is_file():  # noqa: ASYNC240 -- Credential-file existence check, local metadata
            try:
                # hard-delete: out-of-scope -- Provider credentials are outside projects.
                cred_file.unlink()  # noqa: ASYNC240 -- Delete one credential file, local metadata
                logger.info("已删除凭证文件: %s", cred_file)
            except OSError:
                logger.warning("删除凭证文件失败: %s", cred_file, exc_info=True)
    return Response(status_code=204)


@router.post("/{provider_id}/credentials/{cred_id}/activate", status_code=204)
async def activate_credential(
    provider_id: str,
    cred_id: int,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    _validate_provider(provider_id, _t)
    repo = CredentialRepository(session)
    await _get_credential_or_404(repo, provider_id, cred_id, _t)
    await repo.activate(cred_id, provider_id)
    await session.commit()
    await _invalidate_caches(request)
    return Response(status_code=204)


def _write_vertex_credential_file(dest: Path, contents: bytes) -> None:
    """Atomically persist credentials through a temporary file, restricting POSIX permissions to owner read/write."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(".tmp")
    tmp_path.write_bytes(contents)
    # Windows chmod 0o600 controls only the read-only bit, not other-user access;
    # rely on filesystem ACLs for Windows credentials (user-level %LOCALAPPDATA%).
    if os.name == "posix":
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            logger.warning("无法设置临时凭证文件权限: %s", tmp_path, exc_info=True)
    os.replace(tmp_path, dest)
    if os.name == "posix":
        try:
            os.chmod(dest, 0o600)
        except OSError:
            logger.warning("无法设置凭证文件权限: %s", dest, exc_info=True)


@router.post("/gemini-vertex/credentials/upload", status_code=201, response_model=CredentialResponse)
async def upload_vertex_credential(
    request: Request,
    _t: Translator,
    name: str = "Vertex Credentials",
    session: AsyncSession = Depends(get_async_session),
    file: UploadFile = File(...),
) -> CredentialResponse:
    """Upload a Vertex AI service-account JSON credential file and create its credential record."""
    try:
        contents = await file.read(MAX_VERTEX_CREDENTIALS_BYTES + 1)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_t("vertex_json_read_failed")) from exc

    if len(contents) > MAX_VERTEX_CREDENTIALS_BYTES:
        raise HTTPException(status_code=413, detail=_t("vertex_json_too_large"))

    try:
        payload = json.loads(contents.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_t("vertex_json_invalid")) from exc

    if not isinstance(payload, dict) or not payload.get("project_id"):
        raise HTTPException(status_code=400, detail=_t("vertex_json_missing_project_id"))

    repo = CredentialRepository(session)
    cred = await repo.create(provider="gemini-vertex", name=name)

    dest = DataRootLayout.current().vertex_credential_path(cred.id)
    await run_sync_transaction(_write_vertex_credential_file, dest, contents)

    await session.commit()
    await _invalidate_caches(request)

    await session.refresh(cred)
    # Serialization probes credential-file locations (stat); offload to a thread.
    return await asyncio.to_thread(_cred_to_response, cred)


# ---------------------------------------------------------------------------
# Connectivity checks by provider.
# ---------------------------------------------------------------------------

_CONNECTIVITY_CHECK_TIMEOUT = 15  # Seconds.


def _check_gemini_aistudio(config: dict[str, str], _t: Callable[..., str]) -> ConnectivityCheckResponse:
    """Validate Gemini AI Studio API keys through models.list()."""
    from google import genai

    api_key = config["api_key"]
    base_url = normalize_base_url(config.get("base_url"))
    http_options: genai.types.HttpOptionsDict | None = {"base_url": base_url} if base_url else None
    client = genai.Client(api_key=api_key, http_options=http_options)

    pager = client.models.list()
    available = _extract_gemini_models(pager)
    return ConnectivityCheckResponse(
        success=True,
        available_models=available,
        message=_t("connectivity_check_ok"),
    )


def _check_gemini_vertex(config: dict[str, str], _t: Callable[..., str]) -> ConnectivityCheckResponse:
    """Check connectivity using Vertex AI credentials."""
    from google import genai
    from google.oauth2 import service_account

    credentials_path = config.get("credentials_path", "")
    if not credentials_path or not Path(credentials_path).is_file():
        return ConnectivityCheckResponse(
            success=False,
            available_models=[],
            message=_t("file_not_found", path=credentials_path),
        )

    with open(credentials_path, encoding="utf-8") as f:
        creds_data = json.load(f)

    project_id = creds_data.get("project_id")
    if not project_id:
        return ConnectivityCheckResponse(
            success=False,
            available_models=[],
            message=_t("vertex_json_missing_project_id"),
        )

    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=VERTEX_SCOPES,
    )
    client = genai.Client(
        vertexai=True,
        project=project_id,
        location="global",
        credentials=credentials,
    )

    pager = client.models.list()
    available = _extract_gemini_models(pager)
    return ConnectivityCheckResponse(
        success=True,
        available_models=available,
        message=_t("connectivity_check_ok"),
    )


def _extract_gemini_models(pager) -> list[str]:
    """Extract video/image-related models from Gemini models.list(), removing path prefixes."""
    keywords = ("veo", "imagen", "image")
    models: set[str] = set()
    for m in pager:
        name = m.name or ""
        if not any(k in name.lower() for k in keywords):
            continue
        # Remove "models/" or "publishers/google/models/" prefixes.
        short = name.rsplit("/", 1)[-1]
        models.add(short)
    return sorted(models)


def _check_ark(config: dict[str, str], _t: Callable[..., str]) -> ConnectivityCheckResponse:
    """Validate Ark API keys through tasks.list."""
    from lib.backends.ark_shared import create_ark_client

    client = create_ark_client(api_key=config["api_key"], base_url=config.get("base_url"))
    # Lightweight connectivity check without creating resources.
    client.content_generation.tasks.list(page_size=1)
    return ConnectivityCheckResponse(
        success=True,
        available_models=[],
        message=_t("connectivity_check_ok"),
    )


def _check_grok(config: dict[str, str], _t: Callable[..., str]) -> ConnectivityCheckResponse:
    """Validate xAI API keys through models.list_language_models()."""
    import xai_sdk

    client = xai_sdk.Client(api_key=config["api_key"])
    models = client.models.list_language_models()
    available = sorted(m.name for m in models if m.name)
    return ConnectivityCheckResponse(
        success=True,
        available_models=available,
        message=_t("connectivity_check_ok"),
    )


_OPENAI_MODEL_KEYWORDS = ("gpt", "sora", "dall", "o1", "o3", "o4")


def _check_openai(config: dict[str, str], _t: Callable[..., str]) -> ConnectivityCheckResponse:
    """Validate OpenAI API keys through models.list()."""
    from openai import OpenAI

    kwargs: dict = {"api_key": config["api_key"]}
    base_url = config.get("base_url")
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    models = client.models.list()
    available = sorted(m.id for m in models.data if any(k in m.id.lower() for k in _OPENAI_MODEL_KEYWORDS))
    return ConnectivityCheckResponse(
        success=True,
        available_models=available,
        message=_t("connectivity_check_ok"),
    )


def _check_vidu(config: dict[str, str], _t: Callable[..., str]) -> ConnectivityCheckResponse:
    """Vidu connectivity check; HTTP details live in lib.backends.vidu_shared.test_vidu_connection (fork-only)."""
    from lib.backends.vidu_shared import test_vidu_connection

    test_vidu_connection(config)
    return ConnectivityCheckResponse(
        success=True,
        available_models=[],
        message=_t("connectivity_check_ok"),
    )


def _check_dashscope(config: dict[str, str], _t: Callable[..., str]) -> ConnectivityCheckResponse:
    """Validate DashScope API keys through models.list() (compatible-mode, OpenAI protocol).

    Share custom_provider discovery's OpenAI-compatible mechanism. dashscope_text_base_url derives
    {host}/compatible-mode/v1, accepting hosts or URLs with either suffix."""
    from openai import OpenAI

    from lib.backends.dashscope_shared import dashscope_text_base_url

    client = OpenAI(
        api_key=config["api_key"],
        base_url=dashscope_text_base_url(config.get("base_url")),
    )
    models = client.models.list()
    available = sorted(m.id for m in models.data if "qwen" in m.id.lower() or "wan" in m.id.lower())
    return ConnectivityCheckResponse(
        success=True,
        available_models=available,
        message=_t("connectivity_check_ok"),
    )


def _check_minimax(config: dict[str, str], _t: Callable[..., str]) -> ConnectivityCheckResponse:
    """Validate MiniMax API keys through models.list() (OpenAI-compatible protocol).

    Like DashScope, reuse the OpenAI client for models.list(). minimax_text_base_url normalizes to
    {host}/v1, accepting hosts or /v1-suffixed URLs (domestic site by default, configurable to international)."""
    from openai import OpenAI

    from lib.backends.minimax_shared import minimax_text_base_url

    client = OpenAI(
        api_key=config["api_key"],
        base_url=minimax_text_base_url(config.get("base_url")),
    )
    models = client.models.list()
    available = sorted(m.id for m in models.data if "minimax" in m.id.lower() or "abab" in m.id.lower())
    return ConnectivityCheckResponse(
        success=True,
        available_models=available,
        message=_t("connectivity_check_ok"),
    )


def _check_kling(config: dict[str, str], _t: Callable[..., str]) -> ConnectivityCheckResponse:
    """Validate Kling credentials by querying account resource-package balances (``GET /account/costs``, officially marked free-to-call with no side effects).

    Choose one of two auth modes through kling_auth_mode, shared with backend_assembly._build_kling
    so real generation and connectivity checks always use the same dispatch order. Missing credentials
    cause resolve_kling_api_key / resolve_kling_jwt_credentials ValueError, converted by the outer except
    into explicit connectivity_check_failed wording. account/costs is at the domain root (no /v1 prefix),
    so strip that suffix from base_url."""
    import time

    import httpx

    from lib.backends.kling_shared import (
        KLING_BASE_URL,
        KlingJWTManager,
        kling_auth_mode,
        kling_bearer_headers,
        kling_response_error,
        resolve_kling_api_key,
        resolve_kling_jwt_credentials,
    )

    if kling_auth_mode(config) == "bearer":
        headers = kling_bearer_headers(resolve_kling_api_key(config.get("api_key")))
    else:
        access_key, secret_key = resolve_kling_jwt_credentials(config.get("access_key"), config.get("secret_key"))
        headers = KlingJWTManager(access_key, secret_key).auth_headers()

    base_url = (config.get("base_url") or KLING_BASE_URL).rstrip("/")
    root = base_url.removesuffix("/v1")
    now_ms = int(time.time() * 1000)
    one_day_ms = 24 * 60 * 60 * 1000
    resp = httpx.get(
        f"{root}/account/costs",
        params={"start_time": now_ms - one_day_ms, "end_time": now_ms},
        headers=headers,
        timeout=_CONNECTIVITY_CHECK_TIMEOUT,
    )
    # Parse JSON errors before raise_for_status: Kling returns JSON bodies on auth failures too (business code +
    # message, e.g. missing access key). Extracting first preserves specific reasons; otherwise
    # 4xx/5xx exceptions discard bodies, leaving generic HTTP status wording. Claimed JSON content-type with
    # invalid/empty bodies (e.g. gateway truncation) raises JSONDecodeError. Declared type determines whether to try
    # parsing, not guaranteed validity. On parse failure skip business-error extraction and use
    # raise_for_status fallback, exposing HTTP status instead of masking it with a parsing exception.
    if "application/json" in resp.headers.get("content-type", ""):
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if payload is not None:
            err = kling_response_error(payload)
            if err is not None:
                raise RuntimeError(err)
    raise_for_status_redacted(resp)
    return ConnectivityCheckResponse(
        success=True,
        available_models=[],
        message=_t("connectivity_check_ok"),
    )


_CONNECTIVITY_CHECK_DISPATCH: dict[str, Callable[[dict[str, str], Any], ConnectivityCheckResponse]] = {
    "gemini-aistudio": _check_gemini_aistudio,
    "gemini-vertex": _check_gemini_vertex,
    "ark": _check_ark,
    "ark-agent-plan": _check_ark,
    "grok": _check_grok,
    "openai": _check_openai,
    "vidu": _check_vidu,
    "dashscope": _check_dashscope,
    "minimax": _check_minimax,
    "kling": _check_kling,
}


@router.post("/{provider_id}/test", response_model=ConnectivityCheckResponse)
async def check_provider_connectivity(
    provider_id: str,
    _t: Translator,
    locale: Locale,
    credential_id: int | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> ConnectivityCheckResponse:
    """Call provider APIs to check connectivity; credential_id may select a specific credential."""
    _validate_provider(provider_id, _t)

    repo = CredentialRepository(session)
    if credential_id is not None:
        cred = await _get_credential_or_404(repo, provider_id, credential_id, _t)
    else:
        cred = await repo.get_active(provider_id)

    if cred is None:
        return ConnectivityCheckResponse(
            success=False,
            available_models=[],
            message=_t("missing_credentials"),
        )

    svc = ConfigService(session)
    config = await svc.get_provider_config(provider_id)
    cred.overlay_config(config)

    # Mirror simple-family backend base_url priority: when users did not explicitly configure base_url,
    # inject ProviderMeta.default_base_url so connectivity checks hit the correct endpoint.
    if not config.get("base_url"):
        meta = PROVIDER_REGISTRY.get(provider_id)
        if meta and meta.default_base_url:
            config["base_url"] = meta.default_base_url

    check_fn = _CONNECTIVITY_CHECK_DISPATCH.get(provider_id)
    if check_fn is None:
        names = await load_display_names_or_builtin(session, locale)
        return ConnectivityCheckResponse(
            success=False,
            available_models=[],
            message=render_message("connectivity_check_unsupported", {"provider_id": provider_id}, _t, names),
        )

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(check_fn, config, _t),
            timeout=_CONNECTIVITY_CHECK_TIMEOUT,
        )
    except TimeoutError:
        return ConnectivityCheckResponse(
            success=False,
            available_models=[],
            message=_t("connectivity_check_timeout"),
        )
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("连通性检查失败 [%s]: %s", provider_id, err_msg)
        return ConnectivityCheckResponse(
            success=False,
            available_models=[],
            message=_t("connectivity_check_failed", err_msg=err_msg),
        )
    return result
