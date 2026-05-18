"""Async repository for User CRUD.

Fork-private. 与上游 ``lib/db/models/user.py::User`` 对应，schema 不变。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update

from lib.db.base import dt_to_iso, utc_now
from lib.db.models.user import User
from lib.db.repositories.base import BaseRepository


def _row_to_dict(row: User) -> dict[str, Any]:
    return {
        "id": row.id,
        "username": row.username,
        "role": row.role,
        "is_active": bool(row.is_active),
        "created_at": dt_to_iso(row.created_at),
        "updated_at": dt_to_iso(row.updated_at),
    }


class UserRepository(BaseRepository):
    async def list_all(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        stmt = select(User).order_by(User.created_at.asc())
        if not include_inactive:
            stmt = stmt.where(User.is_active.is_(True))
        result = await self.session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars()]

    async def get_by_id(self, user_id: str) -> dict[str, Any] | None:
        stmt = select(User).where(User.id == user_id)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _row_to_dict(row) if row else None

    async def get_by_username(self, username: str) -> dict[str, Any] | None:
        stmt = select(User).where(User.username == username)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _row_to_dict(row) if row else None

    async def create(self, *, username: str, role: str, is_active: bool = True) -> dict[str, Any]:
        row = User(
            id=str(uuid.uuid4()),
            username=username,
            role=role,
            is_active=is_active,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return _row_to_dict(row)

    async def update_role(self, user_id: str, role: str) -> bool:
        result = await self.session.execute(
            update(User).where(User.id == user_id).values(role=role, updated_at=utc_now())
        )
        return result.rowcount > 0

    async def set_active(self, user_id: str, is_active: bool) -> bool:
        result = await self.session.execute(
            update(User).where(User.id == user_id).values(is_active=is_active, updated_at=utc_now())
        )
        return result.rowcount > 0

    async def delete(self, user_id: str) -> bool:
        result = await self.session.execute(sa_delete(User).where(User.id == user_id))
        return result.rowcount > 0
