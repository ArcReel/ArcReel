"""
API Key 管理路由

提供 API Key 的创建、列表查询和删除接口。
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from lib.db import async_session_factory
from lib.db.repositories.api_key_repository import ApiKeyRepository
from server.auth import get_current_user, invalidate_api_key_cache

router = APIRouter()


def _require_jwt_auth(user: dict) -> None:
    """确保请求通过 JWT 认证（非 API Key）。API Key 管理操作不允许由 API Key 本身执行。"""
    if user.get("via") == "apikey":
        raise HTTPException(status_code=403, detail="API Key 无权执行此操作，请使用 JWT 认证")


API_KEY_PREFIX = "arc-"
API_KEY_RANDOM_BYTES = 32  # 生成 64 hex 字符，取前 32 → 36 字符总长
API_KEY_DEFAULT_EXPIRY_DAYS = 30


def _generate_api_key() -> str:
    """生成格式为 arc-<32位随机字符> 的 API Key。"""
    random_part = secrets.token_hex(16)  # 32 hex chars
    return f"{API_KEY_PREFIX}{random_part}"


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _default_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=API_KEY_DEFAULT_EXPIRY_DAYS)


class CreateApiKeyRequest(BaseModel):
    name: str
    expires_days: Optional[int] = Field(None, ge=0)  # None 使用默认 30 天，0 表示不过期


class CreateApiKeyResponse(BaseModel):
    id: int
    name: str
    key: str  # 完整 key，仅在创建时返回
    key_prefix: str
    created_at: str
    expires_at: Optional[str]


class ApiKeyInfo(BaseModel):
    id: int
    name: str
    key_prefix: str
    created_at: str
    expires_at: Optional[str]
    last_used_at: Optional[str]


@router.post("/api-keys", status_code=201)
async def create_api_key(
    body: CreateApiKeyRequest,
    _user: Annotated[dict, Depends(get_current_user)],
) -> CreateApiKeyResponse:
    """创建新 API Key。完整 key 仅在响应中出现一次，之后无法再查看。"""
    _require_jwt_auth(_user)
    key = _generate_api_key()
    key_hash = _hash_key(key)
    key_prefix = key[:8]  # e.g. "arc-abcd"

    if body.expires_days == 0:
        expires_at: Optional[datetime] = None
    elif body.expires_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_days)
    else:
        expires_at = _default_expires_at()

    try:
        async with async_session_factory() as session:
            async with session.begin():
                repo = ApiKeyRepository(session)
                row = await repo.create(
                    name=body.name,
                    key_hash=key_hash,
                    key_prefix=key_prefix,
                    expires_at=expires_at,
                )
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"名称 '{body.name}' 已存在")

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
    _user: Annotated[dict, Depends(get_current_user)],
) -> list[ApiKeyInfo]:
    """查询所有 API Key 的元数据（不含完整 key）。"""
    _require_jwt_auth(_user)
    async with async_session_factory() as session:
        async with session.begin():
            repo = ApiKeyRepository(session)
            rows = await repo.list_all()

    return [ApiKeyInfo(**row) for row in rows]


@router.delete("/api-keys/{key_id}", status_code=204)
async def delete_api_key(
    key_id: int,
    _user: Annotated[dict, Depends(get_current_user)],
) -> None:
    """删除（吊销）指定 API Key，并立即清除内存缓存。"""
    _require_jwt_auth(_user)
    async with async_session_factory() as session:
        async with session.begin():
            repo = ApiKeyRepository(session)
            row = await repo.get_by_id(key_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"API Key {key_id} 不存在")
            key_hash = row["key_hash"]
            # 先失效缓存再删库：即使事务提交后崩溃，缓存也已清除，
            # 不会出现 DB 已删但缓存仍有效的宽限窗口。
            invalidate_api_key_cache(key_hash)
            deleted = await repo.delete(key_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"API Key {key_id} 不存在")
