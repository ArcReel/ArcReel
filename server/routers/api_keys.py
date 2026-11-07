"""API key management routes.

Provide API key creation, listing and deletion endpoints."""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from lib.db import async_session_factory
from lib.db.repositories.api_key_repository import ApiKeyRepository
from server.auth import (
    API_KEY_PREFIX,
    CurrentUser,
    CurrentUserInfo,
    hash_api_key,
    invalidate_api_key_cache,
)
from server.i18n import Translator

router = APIRouter()


def _require_jwt_auth(user: CurrentUserInfo, _t: Callable[..., str]) -> None:
    """Require JWT authentication rather than API key authentication. API keys may not perform key-management operations themselves."""
    if user.sub.startswith("apikey:"):
        raise HTTPException(status_code=403, detail=_t("jwt_auth_required"))


API_KEY_DEFAULT_EXPIRY_DAYS = 30


def _generate_api_key() -> str:
    """Generate an API key formatted as arc-<32 random characters>."""
    random_part = secrets.token_hex(16)  # 32 hex chars
    return f"{API_KEY_PREFIX}{random_part}"


def _default_expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(days=API_KEY_DEFAULT_EXPIRY_DAYS)


class CreateApiKeyRequest(BaseModel):
    name: str
    expires_days: int | None = Field(None, ge=0)  # None uses the 30-day default; 0 means no expiration.


class CreateApiKeyResponse(BaseModel):
    id: int
    name: str
    key: str  # Full key, returned only at creation.
    key_prefix: str
    created_at: str
    expires_at: str | None


class ApiKeyInfo(BaseModel):
    id: int
    name: str
    key_prefix: str
    created_at: str
    expires_at: str | None
    last_used_at: str | None


@router.post("/api-keys", status_code=201)
async def create_api_key(
    body: CreateApiKeyRequest,
    user: CurrentUser,
    _t: Translator,
) -> CreateApiKeyResponse:
    """Create a new API key. Return the full key only once in this response; it cannot be viewed again afterward."""
    _require_jwt_auth(user, _t)
    key = _generate_api_key()
    key_hash = hash_api_key(key)
    key_prefix = key[:8]  # e.g. "arc-abcd"

    if body.expires_days == 0:
        expires_at: datetime | None = None
    elif body.expires_days is not None:
        expires_at = datetime.now(UTC) + timedelta(days=body.expires_days)
    else:
        expires_at = _default_expires_at()

    try:
        async with async_session_factory() as session, session.begin():
            repo = ApiKeyRepository(session)
            row = await repo.create(
                name=body.name,
                key_hash=key_hash,
                key_prefix=key_prefix,
                expires_at=expires_at,
            )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=_t("api_key_name_exists", name=body.name)) from exc

    return CreateApiKeyResponse(
        id=row["id"],
        name=row["name"],
        key=key,
        key_prefix=row["key_prefix"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


@router.get("/api-keys")
async def list_api_keys(
    user: CurrentUser,
    _t: Translator,
) -> list[ApiKeyInfo]:
    """List metadata for all API keys, excluding full keys."""
    _require_jwt_auth(user, _t)
    async with async_session_factory() as session, session.begin():
        repo = ApiKeyRepository(session)
        rows = await repo.list_all()

    return [ApiKeyInfo(**row) for row in rows]


@router.delete("/api-keys/{key_id}", status_code=204)
async def delete_api_key(
    key_id: int,
    user: CurrentUser,
    _t: Translator,
) -> None:
    """Delete (revoke) the specified API key and immediately clear its memory cache."""
    _require_jwt_auth(user, _t)
    async with async_session_factory() as session, session.begin():
        repo = ApiKeyRepository(session)
        row = await repo.get_by_id(key_id)
        if row is None:
            raise HTTPException(status_code=404, detail=_t("api_key_not_found", key_id=key_id))
        key_hash = row["key_hash"]
        # Invalidate the cache before deleting from the database: even a crash after commit leaves the cache clear,
        # avoiding a grace window where the database entry is gone but the cache remains valid.
        invalidate_api_key_cache(key_hash)
        deleted = await repo.delete(key_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=_t("api_key_not_found", key_id=key_id))
