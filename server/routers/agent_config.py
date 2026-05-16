"""Agent Anthropic 凭证 + 预设供应商目录 API。

路由前缀: /api/v1/agent
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.agent_provider_catalog import CUSTOM_SENTINEL_ID, get_preset, list_presets
from lib.config.anthropic_probe import DiagnosisCode, run_test
from lib.config.anthropic_probe import ProbeResult as ProbeResultDC
from lib.config.anthropic_probe import TestConnectionResponse as TestConnectionResponseDC
from lib.config.repository import mask_secret
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.repositories.agent_credential_repo import AgentCredentialRepository
from lib.i18n import Translator
from server.auth import CurrentUser

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
async def list_preset_providers(_user: CurrentUser, _t: Translator) -> PresetProvidersResponse:
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
    is_active: bool
    created_at: str | None


class CredentialListResponse(BaseModel):
    credentials: list[CredentialResponse]


class CreateCredentialRequest(BaseModel):
    preset_id: str
    display_name: str | None = None
    base_url: str | None = None
    api_key: str
    model: str | None = None
    haiku_model: str | None = None
    sonnet_model: str | None = None
    opus_model: str | None = None
    subagent_model: str | None = None
    activate: bool | None = None  # None = 自动 (无 active 时自动 set active)


class UpdateCredentialRequest(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    haiku_model: str | None = None
    sonnet_model: str | None = None
    opus_model: str | None = None
    subagent_model: str | None = None


def _cred_to_response(cred) -> CredentialResponse:
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
        is_active=cred.is_active,
        created_at=dt_to_iso(cred.created_at),
    )


# ── Credential endpoints ───────────────────────────────────────────


@router.get("/credentials", response_model=CredentialListResponse)
async def list_credentials(
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialListResponse:
    repo = AgentCredentialRepository(session)
    creds = await repo.list_for_user()
    return CredentialListResponse(credentials=[_cred_to_response(c) for c in creds])


@router.post("/credentials", response_model=CredentialResponse, status_code=201)
async def create_credential(
    body: CreateCredentialRequest,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialResponse:
    if body.preset_id != CUSTOM_SENTINEL_ID:
        preset = get_preset(body.preset_id)
        if preset is None:
            raise HTTPException(status_code=422, detail=_t("agent_preset_unknown", preset_id=body.preset_id))
        base_url = body.base_url or preset.messages_url
        display_name = body.display_name or preset.display_name
        model = body.model or preset.default_model
    else:
        if not body.base_url:
            raise HTTPException(status_code=422, detail=_t("agent_base_url_required_custom"))
        base_url = body.base_url
        display_name = body.display_name or "Custom"
        model = body.model

    repo = AgentCredentialRepository(session)
    cred = await repo.create(
        preset_id=body.preset_id,
        display_name=display_name,
        base_url=base_url,
        api_key=body.api_key,
        model=model,
        haiku_model=body.haiku_model,
        sonnet_model=body.sonnet_model,
        opus_model=body.opus_model,
        subagent_model=body.subagent_model,
    )
    # 自动 active 策略：activate=True，或 (activate=None 且当前无 active)
    should_activate = body.activate is True
    if body.activate is None:
        existing_active = await repo.get_active()
        if existing_active is None:
            should_activate = True
    if should_activate:
        await repo.set_active(cred.id)
    await session.commit()
    await session.refresh(cred)
    return _cred_to_response(cred)


@router.patch("/credentials/{cred_id}", response_model=CredentialResponse)
async def update_credential(
    cred_id: int,
    body: UpdateCredentialRequest,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialResponse:
    repo = AgentCredentialRepository(session)
    # exclude_unset 保留客户端显式传入的 None（用于清空 model/haiku_model 等可选覆盖项）
    # 必需字段 (display_name/base_url/api_key) 仍过滤 None：传 null 给它们没有意义
    fields = body.model_dump(exclude_unset=True)
    for required in ("display_name", "base_url", "api_key"):
        if fields.get(required) is None:
            fields.pop(required, None)
    if not fields:
        raise HTTPException(status_code=400, detail=_t("agent_no_fields_to_update"))
    cred = await repo.update(cred_id, **fields)
    if cred is None:
        raise HTTPException(status_code=404, detail=_t("agent_credential_not_found"))
    await session.commit()
    return _cred_to_response(cred)


@router.delete("/credentials/{cred_id}", status_code=204)
async def delete_credential(
    cred_id: int,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    repo = AgentCredentialRepository(session)
    try:
        await repo.delete(cred_id)
    except ValueError as exc:
        if "active" in str(exc):
            raise HTTPException(status_code=409, detail=_t("agent_cannot_delete_active")) from exc
        raise HTTPException(status_code=404, detail=_t("agent_credential_not_found")) from exc
    await session.commit()


# ── Activate endpoint ──────────────────────────────────────────────


class ActivateResponse(BaseModel):
    active_id: int


@router.post("/credentials/{cred_id}/activate", response_model=ActivateResponse)
async def activate_credential(
    cred_id: int,
    _user: CurrentUser,
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
    kind: Literal["replace_base_url", "check_api_key", "run_discovery", "see_docs"]
    suggested_value: str | None = None


class TestConnectionResponseModel(BaseModel):
    overall: Literal["ok", "warn", "fail"]
    messages_probe: ProbeResultModel | None
    discovery_probe: ProbeResultModel | None
    diagnosis: str | None
    suggestion: SuggestionModel | None
    derived_messages_root: str
    derived_discovery_root: str


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
        discovery_probe=_serialize_probe(r.discovery_probe),
        diagnosis=r.diagnosis.value if isinstance(r.diagnosis, DiagnosisCode) else None,
        suggestion=SuggestionModel(kind=r.suggestion.kind, suggested_value=r.suggestion.suggested_value)
        if r.suggestion
        else None,
        derived_messages_root=r.derived_messages_root,
        derived_discovery_root=r.derived_discovery_root,
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
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=_t("agent_test_validation_error", error=str(exc))) from exc
    return _serialize_test_response(result)


@router.post("/test-connection", response_model=TestConnectionResponseModel)
async def test_connection_draft(
    body: TestConnectionRequest,
    _user: CurrentUser,
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
async def test_credential(
    cred_id: int,
    _user: CurrentUser,
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
