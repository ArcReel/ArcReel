"""Async repository for API Key management."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete as sa_delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.api_key import ApiKey


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _row_to_dict(row: ApiKey) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "key_prefix": row.key_prefix,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
    }


class ApiKeyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        name: str,
        key_hash: str,
        key_prefix: str,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a new API key record."""
        row = ApiKey(
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            created_at=_utc_now_iso(),
            expires_at=expires_at,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return _row_to_dict(row)

    async def list_all(self) -> list[dict[str, Any]]:
        """Return all API keys (metadata only, no hashes)."""
        result = await self.session.execute(
            select(ApiKey).order_by(ApiKey.created_at.desc())
        )
        return [_row_to_dict(r) for r in result.scalars()]

    async def get_by_hash(self, key_hash: str) -> Optional[dict[str, Any]]:
        """Look up a key by its SHA-256 hash. Returns full row including hash."""
        result = await self.session.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "key_hash": row.key_hash,
            "key_prefix": row.key_prefix,
            "created_at": row.created_at,
            "expires_at": row.expires_at,
            "last_used_at": row.last_used_at,
        }

    async def get_by_id(self, key_id: int) -> Optional[dict[str, Any]]:
        """Look up a key by its primary key ID. Includes key_hash for cache invalidation."""
        result = await self.session.execute(
            select(ApiKey).where(ApiKey.id == key_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        d = _row_to_dict(row)
        d["key_hash"] = row.key_hash
        return d

    async def delete(self, key_id: int) -> bool:
        """Delete a key by ID. Returns True if deleted, False if not found."""
        result = await self.session.execute(
            sa_delete(ApiKey).where(ApiKey.id == key_id)
        )
        return result.rowcount > 0

    async def touch_last_used(self, key_hash: str) -> None:
        """Update last_used_at for the given key hash."""
        await self.session.execute(
            update(ApiKey)
            .where(ApiKey.key_hash == key_hash)
            .values(last_used_at=_utc_now_iso())
        )
