"""Agent Anthropic credentials and preset-provider catalog API.

Route prefix: /api/v1/agent"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lib.agent.agent_provider_catalog import CUSTOM_SENTINEL_ID, get_preset, list_presets
from lib.agent.credential_tiers import Tier, tier_statuses
from lib.catalog import query as catalog
from lib.config.anthropic_probe import DiagnosisCode, run_test
from lib.config.anthropic_probe import ProbeResult as ProbeResultDC
from lib.config.anthropic_probe import TestConnectionResponse as TestConnectionResponseDC
from lib.config.repository import SystemSettingRepository, mask_secret
from lib.config.url_utils import InvalidAnthropicBaseUrlError, validate_anthropic_base_url
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.agent_credential import AgentAnthropicCredential
from lib.db.repositories.agent_credential_repo import AgentCredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.infra.api_errors import UnprocessableError
from server.i18n import Translator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent 配置"])


# ── Response models ─────────────────────────────────────────────────


class PresetProviderResponse(BaseModel):
    id: str
    display_name: str
    icon_key: str
    messages_url: str
    discovery_url: str | None
    default_model: str
    suggested_models: list[str]
    docs_url: str | None
    api_key_url: str | None
    notes: str | None
    api_key_pattern: str | None
    is_recommended: bool


class PresetProvidersResponse(BaseModel):
    providers: list[PresetProviderResponse]
    custom_sentinel_id: str


# ── Endpoints ──────────────────────────────────────────────────────


@router.get("/preset-providers", response_model=PresetProvidersResponse)
async def list_preset_providers(_t: Translator) -> PresetProvidersResponse:
    return PresetProvidersResponse(
        providers=[
            PresetProviderResponse(
                id=p.id,
                display_name=p.display_name,
                icon_key=p.icon_key,
                messages_url=p.messages_url,
                discovery_url=p.discovery_url,
                default_model=p.default_model,
                suggested_models=list(p.suggested_models),
                docs_url=p.docs_url,
                api_key_url=p.api_key_url,
                notes=_t(p.notes_i18n_key) if p.notes_i18n_key else None,
                api_key_pattern=p.api_key_pattern,
                is_recommended=p.is_recommended,
            )
            for p in list_presets()
        ],
        custom_sentinel_id=CUSTOM_SENTINEL_ID,
    )


# ── Credential models ──────────────────────────────────────────────


class TierStatusResponse(BaseModel):
    """One tier’s pricing state, queried from the current catalog. ``model_name`` None means an empty tier whose model is chosen by the CLI."""

    tier: Tier
    model_name: str | None
    entry_display_name: str | None
    manually_bound: bool
    # A tier may retain a binding to a missing entry; matching falls back to model name, but UI must still allow unbinding.
    binding_stale: bool
    priced: bool


class CredentialResponse(BaseModel):
    id: int
    preset_id: str
    display_name: str
    icon_key: str | None
    base_url: str
    api_key_masked: str
    model: str | None
    haiku_model: str | None
    sonnet_model: str | None
    opus_model: str | None
    subagent_model: str | None
    multiplier: float
    # Actual model names and pricing states for main, haiku, sonnet, opus and subagent tiers, in fixed order.
    tiers: list[TierStatusResponse]
    is_active: bool
    created_at: str | None


class CredentialListResponse(BaseModel):
    credentials: list[CredentialResponse]


class CreateCredentialRequest(BaseModel):
    preset_id: str
    display_name: str | None = None
    base_url: str | None = None
    # Mutually exclusive with from_custom_provider_id; with a provider ID, the server copies that provider’s key.
    api_key: str | None = None
    from_custom_provider_id: int | None = None
    model: str | None = None
    haiku_model: str | None = None
    sonnet_model: str | None = None
    opus_model: str | None = None
    subagent_model: str | None = None
    # Agent credential multiplier (docs/adr/0100): subscription gateways may use 0 to record zero cost.
    multiplier: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    # Tier → entry bindings (model maker/official model name, docs/adr/0105); ignore tiers with null values.
    tier_bindings: dict[Tier, str | None] | None = None
    activate: bool | None = None  # None = automatic (set active automatically when none is active).


class UpdateCredentialRequest(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    haiku_model: str | None = None
    sonnet_model: str | None = None
    opus_model: str | None = None
    subagent_model: str | None = None
    multiplier: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    # Merge by tier: bind supplied tiers to entries, unbind null tiers and preserve omitted tiers.
    tier_bindings: dict[Tier, str | None] | None = None


class TierBindingRequest(BaseModel):
    """Select a model entry for one tier: model maker/official model name, requiring a currently existing catalog text-model entry."""

    entry_binding: str


def _cred_to_response(cred: AgentAnthropicCredential, settings: dict[str, str]) -> CredentialResponse:
    """``settings`` contains system settings; empty credential tiers fall back to legacy values there, matching injected session environment variables."""
    preset = get_preset(cred.preset_id) if cred.preset_id != CUSTOM_SENTINEL_ID else None
    return CredentialResponse(
        id=cred.id,
        preset_id=cred.preset_id,
        display_name=cred.display_name,
        icon_key=preset.icon_key if preset else None,
        base_url=cred.base_url,
        api_key_masked=mask_secret(cred.api_key),
        model=cred.model,
        haiku_model=cred.haiku_model,
        sonnet_model=cred.sonnet_model,
        opus_model=cred.opus_model,
        subagent_model=cred.subagent_model,
        multiplier=cred.multiplier,
        tiers=[
            TierStatusResponse(
                tier=status.tier,
                model_name=status.model_name,
                entry_display_name=status.entry_display_name,
                manually_bound=status.manually_bound,
                binding_stale=status.binding_stale,
                priced=status.priced,
            )
            for status in tier_statuses(cred, settings)
        ],
        is_active=cred.is_active,
        created_at=dt_to_iso(cred.created_at),
    )


def _validated_tier_bindings(bindings: dict[Tier, str | None] | None, _t: Translator) -> dict[Tier, str | None]:
    """Write validation: new bindings must point to existing catalog text entries.

    Validate only values in this submission. Stored bindings are not echoed on save because tiers merge; discontinued bound entries
    do not block unrelated edits."""
    validated: dict[Tier, str | None] = {}
    for tier, ref in (bindings or {}).items():
        ref = (ref or "").strip() or None
        if ref is not None and catalog.find_bound_entry(ref, "text") is None:
            raise HTTPException(status_code=422, detail=_t("agent_tier_binding_invalid"))
        validated[tier] = ref
    return validated


def _normalized_base_url(raw: str, _t: Translator) -> str:
    """Normalize and validate before persistence; stored values are the actual Claude CLI call values.

    Use fixed error messages without echoing input, because addresses may contain accidentally entered api_key values."""
    try:
        return validate_anthropic_base_url(raw)
    except InvalidAnthropicBaseUrlError as exc:
        raise HTTPException(status_code=422, detail=_t("agent_base_url_invalid")) from exc


async def _resolve_key_source(
    body: CreateCredentialRequest, _t: Translator, session: AsyncSession
) -> tuple[str, str | None]:
    """Resolve new credentials as (api_key, base_url awaiting normalization).

    With from_custom_provider_id, copy that provider’s key and retain its URL if base_url is absent.
    Otherwise take the key from the request body. The two sources are mutually exclusive."""
    if body.from_custom_provider_id is None:
        if not body.api_key:
            raise HTTPException(status_code=422, detail=_t("agent_api_key_required"))
        return body.api_key, body.base_url
    if body.api_key is not None:
        raise HTTPException(status_code=422, detail=_t("agent_api_key_source_conflict"))
    provider = await CustomProviderRepository(session).get_provider(body.from_custom_provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    if not provider.api_key:
        raise HTTPException(status_code=422, detail=_t("agent_import_provider_no_key"))
    return provider.api_key, body.base_url or provider.base_url


# ── Credential endpoints ───────────────────────────────────────────


@router.get("/credentials", response_model=CredentialListResponse)
async def list_credentials(
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialListResponse:
    repo = AgentCredentialRepository(session)
    creds = await repo.list_for_user()
    settings = await SystemSettingRepository(session).get_all()
    return CredentialListResponse(credentials=[_cred_to_response(c, settings) for c in creds])


@router.post("/credentials", response_model=CredentialResponse, status_code=201)
async def create_credential(
    body: CreateCredentialRequest,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialResponse:
    api_key, requested_base_url = await _resolve_key_source(body, _t, session)
    if body.preset_id != CUSTOM_SENTINEL_ID:
        preset = get_preset(body.preset_id)
        if preset is None:
            raise HTTPException(status_code=422, detail=_t("agent_preset_unknown", preset_id=body.preset_id))
        base_url = _normalized_base_url(requested_base_url, _t) if requested_base_url else preset.messages_url
        display_name = body.display_name or preset.display_name
        model = body.model or preset.default_model
    else:
        if not requested_base_url:
            raise HTTPException(status_code=422, detail=_t("agent_base_url_required_custom"))
        base_url = _normalized_base_url(requested_base_url, _t)
        display_name = body.display_name or "Custom"
        model = body.model

    bindings = _validated_tier_bindings(body.tier_bindings, _t)
    repo = AgentCredentialRepository(session)
    cred = await repo.create(
        preset_id=body.preset_id,
        display_name=display_name,
        base_url=base_url,
        api_key=api_key,
        model=model,
        haiku_model=body.haiku_model,
        sonnet_model=body.sonnet_model,
        opus_model=body.opus_model,
        subagent_model=body.subagent_model,
        multiplier=body.multiplier,
        tier_bindings={tier: ref for tier, ref in bindings.items() if ref is not None},
    )
    # Automatic activation: activate=True, or activate=None with no current active credential.
    should_activate = body.activate is True
    if body.activate is None:
        existing_active = await repo.get_active()
        if existing_active is None:
            should_activate = True
    if should_activate:
        await repo.set_active(cred.id)
    await session.commit()
    await session.refresh(cred)
    return _cred_to_response(cred, await SystemSettingRepository(session).get_all())


@router.patch("/credentials/{cred_id}", response_model=CredentialResponse)
async def update_credential(
    cred_id: int,
    body: UpdateCredentialRequest,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialResponse:
    repo = AgentCredentialRepository(session)
    # exclude_unset preserves explicit None for clearing optional model/haiku_model overrides.
    # Still filter None for required display_name/base_url/api_key/multiplier fields, where null has no meaning.
    fields = body.model_dump(exclude_unset=True)
    for required in ("display_name", "base_url", "api_key", "multiplier"):
        if fields.get(required) is None:
            fields.pop(required, None)
    binding_changes = _validated_tier_bindings(fields.pop("tier_bindings", None), _t)
    if "base_url" in fields:
        fields["base_url"] = _normalized_base_url(fields["base_url"], _t)
    if not fields and not binding_changes:
        raise HTTPException(status_code=400, detail=_t("agent_no_fields_to_update"))
    cred = await repo.update(cred_id, **fields)
    if cred is not None and binding_changes:
        cred = await repo.merge_tier_bindings(cred_id, binding_changes)
    if cred is None:
        raise HTTPException(status_code=404, detail=_t("agent_credential_not_found"))
    await session.commit()
    return _cred_to_response(cred, await SystemSettingRepository(session).get_all())


@router.put("/credentials/{cred_id}/tiers/{tier}/binding", response_model=CredentialResponse)
async def bind_tier(
    cred_id: int,
    tier: Tier,
    body: TierBindingRequest,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialResponse:
    """Select a model entry for one tier. Bindings exist only on credentials, without modifying entries or affecting official synchronization."""
    changes = _validated_tier_bindings({tier: body.entry_binding}, _t)
    if changes[tier] is None:
        raise HTTPException(status_code=422, detail=_t("agent_tier_binding_invalid"))
    cred = await AgentCredentialRepository(session).merge_tier_bindings(cred_id, changes)
    if cred is None:
        raise HTTPException(status_code=404, detail=_t("agent_credential_not_found"))
    await session.commit()
    return _cred_to_response(cred, await SystemSettingRepository(session).get_all())


@router.delete("/credentials/{cred_id}/tiers/{tier}/binding", response_model=CredentialResponse)
async def unbind_tier(
    cred_id: int,
    tier: Tier,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialResponse:
    """Unbind one tier and return to automatic name matching; succeed even if the tier was already unbound."""
    cred = await AgentCredentialRepository(session).merge_tier_bindings(cred_id, {tier: None})
    if cred is None:
        raise HTTPException(status_code=404, detail=_t("agent_credential_not_found"))
    await session.commit()
    return _cred_to_response(cred, await SystemSettingRepository(session).get_all())


@router.delete("/credentials/{cred_id}", status_code=204)
async def delete_credential(
    cred_id: int,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    repo = AgentCredentialRepository(session)
    try:
        deleted = await repo.delete(cred_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=_t("agent_cannot_delete_active")) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=_t("agent_credential_not_found"))
    await session.commit()


# ── Activate endpoint ──────────────────────────────────────────────


class ActivateResponse(BaseModel):
    active_id: int


@router.post("/credentials/{cred_id}/activate", response_model=ActivateResponse)
async def activate_credential(
    cred_id: int,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ActivateResponse:
    repo = AgentCredentialRepository(session)
    try:
        await repo.set_active(cred_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=_t("agent_credential_not_found")) from exc
    await session.commit()
    return ActivateResponse(active_id=cred_id)


# ── Test connection models + endpoints ────────────────────────────


class ProbeResultModel(BaseModel):
    success: bool
    status_code: int | None
    latency_ms: int | None
    error: str | None


class SuggestionModel(BaseModel):
    kind: Literal["check_api_key", "run_discovery", "see_docs"]
    suggested_value: str | None = None


class TestConnectionResponseModel(BaseModel):
    overall: Literal["ok", "fail"]
    messages_probe: ProbeResultModel | None
    diagnosis: str | None
    suggestion: SuggestionModel | None
    messages_url: str


class TestConnectionRequest(BaseModel):
    preset_id: str | None = None
    base_url: str | None = None
    api_key: str
    model: str | None = None


def _serialize_probe(p: ProbeResultDC | None) -> ProbeResultModel | None:
    if p is None:
        return None
    return ProbeResultModel(success=p.success, status_code=p.status_code, latency_ms=p.latency_ms, error=p.error)


def _serialize_test_response(r: TestConnectionResponseDC) -> TestConnectionResponseModel:
    return TestConnectionResponseModel(
        overall=r.overall,
        messages_probe=_serialize_probe(r.messages_probe),
        diagnosis=r.diagnosis.value if isinstance(r.diagnosis, DiagnosisCode) else None,
        suggestion=SuggestionModel(kind=r.suggestion.kind, suggested_value=r.suggestion.suggested_value)
        if r.suggestion
        else None,
        messages_url=r.messages_url,
    )


async def _run_and_serialize(
    *,
    preset_id: str | None,
    base_url: str | None,
    api_key: str,
    model: str | None,
    _t: Translator,
) -> TestConnectionResponseModel:
    try:
        result = await run_test(preset_id=preset_id, base_url=base_url, api_key=api_key, model=model)
    except InvalidAnthropicBaseUrlError as exc:
        raise HTTPException(status_code=422, detail=_t("agent_base_url_invalid")) from exc
    except ValueError as exc:
        raise UnprocessableError("agent_test_validation_error").with_diagnostic(str(exc)) from exc
    return _serialize_test_response(result)


@router.post("/test-connection", response_model=TestConnectionResponseModel)
async def run_draft_connection_test(
    body: TestConnectionRequest,
    _t: Translator,
) -> TestConnectionResponseModel:
    return await _run_and_serialize(
        preset_id=body.preset_id,
        base_url=body.base_url,
        api_key=body.api_key,
        model=body.model,
        _t=_t,
    )


@router.post("/credentials/{cred_id}/test", response_model=TestConnectionResponseModel)
async def run_credential_test(
    cred_id: int,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> TestConnectionResponseModel:
    repo = AgentCredentialRepository(session)
    cred = await repo.get(cred_id)
    if cred is None:
        raise HTTPException(status_code=404, detail=_t("agent_credential_not_found"))
    return await _run_and_serialize(
        preset_id=cred.preset_id,
        base_url=cred.base_url,
        api_key=cred.api_key,
        model=cred.model,
        _t=_t,
    )
