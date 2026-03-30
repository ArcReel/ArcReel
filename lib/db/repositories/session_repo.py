"""Async repository for agent sessions."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update

from lib.db.base import DEFAULT_USER_ID, dt_to_iso, utc_now
from lib.db.models.session import AgentSession
from lib.db.repositories.base import BaseRepository


def _row_to_dict(row: AgentSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "sdk_session_id": row.sdk_session_id,
        "project_name": row.project_name,
        "title": row.title or "",
        "status": row.status,
        "created_at": dt_to_iso(row.created_at),
        "updated_at": dt_to_iso(row.updated_at),
    }


class SessionRepository(BaseRepository):
    async def create(
        self, project_name: str, sdk_session_id: str, title: str = "", user_id: str = DEFAULT_USER_ID
    ) -> dict[str, Any]:
        now = utc_now()
        row = AgentSession(
            id=uuid.uuid4().hex,
            sdk_session_id=sdk_session_id,
            project_name=project_name,
            title=title,
            status="idle",
            created_at=now,
            updated_at=now,
            user_id=user_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return _row_to_dict(row)

    async def get(self, session_id: str) -> dict[str, Any] | None:
        stmt = select(AgentSession).where(AgentSession.sdk_session_id == session_id)
        stmt = self._scope_query(stmt, AgentSession)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return _row_to_dict(row) if row else None

    async def list(
        self,
        *,
        project_name: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        stmt = select(AgentSession)
        if project_name:
            stmt = stmt.where(AgentSession.project_name == project_name)
        if status:
            stmt = stmt.where(AgentSession.status == status)
        stmt = stmt.order_by(AgentSession.updated_at.desc())
        stmt = stmt.limit(max(1, limit)).offset(max(0, offset))
        stmt = self._scope_query(stmt, AgentSession)

        result = await self.session.execute(stmt)
        return [_row_to_dict(row) for row in result.scalars().all()]

    async def update_status(self, session_id: str, status: str) -> bool:
        now = utc_now()
        result = await self.session.execute(
            update(AgentSession).where(AgentSession.sdk_session_id == session_id).values(status=status, updated_at=now)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def delete(self, session_id: str) -> bool:
        result = await self.session.execute(sa_delete(AgentSession).where(AgentSession.sdk_session_id == session_id))
        await self.session.commit()
        return result.rowcount > 0

    async def interrupt_running(self) -> int:
        now = utc_now()
        result = await self.session.execute(
            update(AgentSession).where(AgentSession.status == "running").values(status="interrupted", updated_at=now)
        )
        await self.session.commit()
        return result.rowcount
