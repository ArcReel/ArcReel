"""Async repository for agent sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete as sa_delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.session import AgentSession


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: AgentSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "sdk_session_id": row.sdk_session_id,
        "project_name": row.project_name,
        "title": row.title or "",
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class SessionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, project_name: str, title: str = "") -> dict[str, Any]:
        now = _utc_now_iso()
        row = AgentSession(
            id=uuid.uuid4().hex,
            project_name=project_name,
            title=title,
            status="idle",
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return _row_to_dict(row)

    async def get(self, session_id: str) -> Optional[dict[str, Any]]:
        result = await self.session.execute(
            select(AgentSession).where(AgentSession.id == session_id)
        )
        row = result.scalar_one_or_none()
        return _row_to_dict(row) if row else None

    async def list(
        self,
        *,
        project_name: Optional[str] = None,
        status: Optional[str] = None,
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

        result = await self.session.execute(stmt)
        return [_row_to_dict(row) for row in result.scalars().all()]

    async def update_status(self, session_id: str, status: str) -> bool:
        now = _utc_now_iso()
        result = await self.session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(status=status, updated_at=now)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def update_sdk_session_id(self, session_id: str, sdk_session_id: str) -> bool:
        now = _utc_now_iso()
        result = await self.session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(sdk_session_id=sdk_session_id, updated_at=now)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def update_title(self, session_id: str, title: str) -> bool:
        now = _utc_now_iso()
        result = await self.session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(title=title.strip(), updated_at=now)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def delete(self, session_id: str) -> bool:
        result = await self.session.execute(
            sa_delete(AgentSession).where(AgentSession.id == session_id)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def interrupt_running(self) -> int:
        now = _utc_now_iso()
        result = await self.session.execute(
            update(AgentSession)
            .where(AgentSession.status == "running")
            .values(status="interrupted", updated_at=now)
        )
        await self.session.commit()
        return result.rowcount
