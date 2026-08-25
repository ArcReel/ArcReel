"""Service-to-service account configuration API for the data middle platform."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import mask_secret
from lib.config.url_utils import normalize_base_url
from lib.db import get_async_session
from lib.db.models.user import User
from lib.db.repositories.credential_repository import CredentialRepository
from server.services.account_center import account_center_config
from server.services.account_center_sync import build_config_schema

router = APIRouter(prefix="/account-center/admin", tags=["账号中心子系统配置"])

_SUPPORTED_SECRET_FIELDS = ("api_key", "access_key", "secret_key")


def _stripped(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) else value


StrippedOptional = Annotated[str | None, AfterValidator(_stripped)]


class ManagedCredentialRequest(BaseModel):
    name: str = Field(default="数据中台分配", min_length=1, max_length=128)
    api_key: StrippedOptional = None
    access_key: StrippedOptional = None
    secret_key: StrippedOptional = None
    base_url: StrippedOptional = None


async def require_integration_token(authorization: str | None = Header(None)) -> None:
    provided = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not provided:
        raise HTTPException(
            status_code=401,
            detail={"code": "INTEGRATION_TOKEN_INVALID", "message": "中台集成凭证无效"},
        )
    expected = account_center_config(require_oauth=False).integration_token
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={"code": "INTEGRATION_NOT_CONFIGURED", "message": "ArcReel 尚未配置中台集成凭证"},
        )
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail={"code": "INTEGRATION_TOKEN_INVALID", "message": "中台集成凭证无效"},
        )


IntegrationAuth = Annotated[None, Depends(require_integration_token)]


@router.get("/config-schema")
async def config_schema(_auth: IntegrationAuth):
    return build_config_schema(account_center_config(require_oauth=False).system_id)


@router.get("/users/{account_center_sub}/provider-credentials")
async def list_user_provider_credentials(
    account_center_sub: str,
    _auth: IntegrationAuth,
    session: AsyncSession = Depends(get_async_session),
):
    user = await _bound_user(session, account_center_sub)
    repo = CredentialRepository(session, user.id)
    items = []
    for provider_id in PROVIDER_REGISTRY:
        credential = await repo.get_active(provider_id)
        if credential is None:
            continue
        items.append(
            {
                "provider_id": provider_id,
                "credential_id": credential.id,
                "name": credential.name,
                "api_key_masked": mask_secret(credential.api_key) if credential.api_key else None,
                "access_key_masked": mask_secret(credential.access_key) if credential.access_key else None,
                "secret_key_masked": mask_secret(credential.secret_key) if credential.secret_key else None,
                "base_url": credential.base_url,
                "is_active": credential.is_active,
            }
        )
    return {
        "account_center_sub": account_center_sub,
        "local_user_id": user.id,
        "local_username": user.username,
        "credentials": items,
    }


@router.put("/users/{account_center_sub}/provider-credentials/{provider_id}")
async def put_user_provider_credential(
    account_center_sub: str,
    provider_id: str,
    body: ManagedCredentialRequest,
    _auth: IntegrationAuth,
    session: AsyncSession = Depends(get_async_session),
):
    meta = PROVIDER_REGISTRY.get(provider_id)
    if meta is None:
        raise HTTPException(status_code=404, detail={"code": "PROVIDER_NOT_FOUND", "message": "供应商不存在"})
    user = await _bound_user(session, account_center_sub)
    repo = CredentialRepository(session, user.id)
    credential = await repo.get_active(provider_id)
    allowed_fields = [key for key in meta.secret_keys if key in _SUPPORTED_SECRET_FIELDS]
    values = {
        key: (getattr(body, key) if key in body.model_fields_set else getattr(credential, key, None))
        for key in allowed_fields
    }
    groups = meta.credential_groups or ([allowed_fields] if allowed_fields else [])
    if not groups or not any(all(values.get(key) for key in group) for group in groups):
        raise HTTPException(
            status_code=422,
            detail={"code": "CREDENTIAL_FIELDS_INCOMPLETE", "message": "供应商凭证字段不完整"},
        )
    if credential is None:
        credential = await repo.create(
            provider=provider_id,
            name=body.name,
            api_key=body.api_key if "api_key" in allowed_fields else None,
            access_key=body.access_key if "access_key" in allowed_fields else None,
            secret_key=body.secret_key if "secret_key" in allowed_fields else None,
            base_url=normalize_base_url(body.base_url),
        )
    else:
        updates: dict[str, str | None] = {}
        if "name" in body.model_fields_set:
            updates["name"] = body.name
        for key in allowed_fields:
            if key in body.model_fields_set:
                updates[key] = getattr(body, key)
        if "base_url" in body.model_fields_set:
            updates["base_url"] = normalize_base_url(body.base_url)
        await repo.update(
            credential.id,
            **updates,
        )
    credential.management_source = "account_center"
    await session.commit()
    return {
        "success": True,
        "account_center_sub": account_center_sub,
        "provider_id": provider_id,
        "credential_id": credential.id,
    }


@router.delete("/users/{account_center_sub}/provider-credentials/{provider_id}", status_code=204)
async def delete_user_provider_credential(
    account_center_sub: str,
    provider_id: str,
    _auth: IntegrationAuth,
    session: AsyncSession = Depends(get_async_session),
):
    user = await _bound_user(session, account_center_sub)
    repo = CredentialRepository(session, user.id)
    credential = await repo.get_active(provider_id)
    if credential is not None:
        await repo.delete(credential.id)
        await session.commit()


async def _bound_user(session: AsyncSession, account_center_sub: str) -> User:
    result = await session.execute(select(User).where(User.account_center_sub == account_center_sub))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ACCOUNT_NOT_BOUND",
                "message": "该中台账号尚未完成 ArcReel 首次登录和账号绑定",
            },
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail={"code": "ACCOUNT_DISABLED", "message": "ArcReel 账号已停用"})
    return user
