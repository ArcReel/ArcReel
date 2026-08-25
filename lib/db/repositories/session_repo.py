"""Async repository for agent sessions."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update

from lib.db.base import DEFAULT_USER_ID, dt_to_iso, utc_now
from lib.db.models.session import AgentSession
from lib.db.repositories.base import BaseRepository, rowcount


def _row_to_dict(row: AgentSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "sdk_session_id": row.sdk_session_id,
        "project_name": row.project_name,
        "title": row.title or "",
        "status": row.status,
        "superseded_by": row.superseded_by,
        "fork_parent_session_id": row.fork_parent_session_id,
        "fork_anchor_uuid": row.fork_anchor_uuid,
        "created_at": dt_to_iso(row.created_at),
        "updated_at": dt_to_iso(row.updated_at),
    }


class SessionRepository(BaseRepository):
    def __init__(self, session, *, user_id: str = DEFAULT_USER_ID):
        super().__init__(session)
        self.user_id = user_id

    def _scope_query(self, stmt, model):
        if model is AgentSession:
            return stmt.where(AgentSession.user_id == self.user_id)
        return stmt

    def _ownership_filters(self) -> tuple:
        return (AgentSession.user_id == self.user_id,)

    async def create(
        self,
        project_name: str,
        sdk_session_id: str,
        title: str = "",
        user_id: str | None = None,
        *,
        fork_parent_session_id: str | None = None,
        fork_anchor_uuid: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        row = AgentSession(
            id=uuid.uuid4().hex,
            sdk_session_id=sdk_session_id,
            project_name=project_name,
            title=title,
            status="idle",
            fork_parent_session_id=fork_parent_session_id,
            fork_anchor_uuid=fork_anchor_uuid,
            created_at=now,
            updated_at=now,
            user_id=user_id or self.user_id,
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
        # 被分叉取代的会话不进列表；数据整行保留，按 sdk_session_id 仍可 get 到。
        stmt = select(AgentSession).where(AgentSession.superseded_by.is_(None))
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
            update(AgentSession)
            .where(AgentSession.sdk_session_id == session_id, *self._ownership_filters())
            .values(status=status, updated_at=now)
        )
        await self.session.commit()
        return rowcount(result) > 0

    async def mark_superseded(self, session_id: str, superseded_by: str) -> bool:
        """标记会话已被取代；已有指针时不改写，返回 False 让调用方按分叉冲突处理。"""
        now = utc_now()
        result = await self.session.execute(
            update(AgentSession)
            .where(
                AgentSession.sdk_session_id == session_id,
                AgentSession.superseded_by.is_(None),
                *self._ownership_filters(),
            )
            .values(superseded_by=superseded_by, updated_at=now)
        )
        await self.session.commit()
        return rowcount(result) > 0

    async def clear_superseded(self, session_id: str, superseded_by: str) -> bool:
        """撤回指向 ``superseded_by`` 的取代指针；指针已指向别的会话时不动。"""
        now = utc_now()
        result = await self.session.execute(
            update(AgentSession)
            .where(
                AgentSession.sdk_session_id == session_id,
                AgentSession.superseded_by == superseded_by,
                *self._ownership_filters(),
            )
            .values(superseded_by=None, updated_at=now)
        )
        await self.session.commit()
        return rowcount(result) > 0

    async def delete(self, session_id: str) -> bool:
        # 指向被删会话的取代指针改指它自己的后继：删的是链中间时前身继续指向仍然
        # 活着的末端，删的是链尾时后继为空、前身随之回到会话列表。不接手则前身会
        # 带着一个指向不存在会话的指针，永久留在列表之外。
        successor = await self.session.scalar(
            select(AgentSession.superseded_by).where(
                AgentSession.sdk_session_id == session_id,
                *self._ownership_filters(),
            )
        )
        owned = await self.session.scalar(
            select(AgentSession.id).where(
                AgentSession.sdk_session_id == session_id,
                *self._ownership_filters(),
            )
        )
        if owned is None:
            return False
        await self.session.execute(
            update(AgentSession)
            .where(AgentSession.superseded_by == session_id, *self._ownership_filters())
            .values(superseded_by=successor, updated_at=utc_now())
        )
        result = await self.session.execute(
            sa_delete(AgentSession).where(
                AgentSession.sdk_session_id == session_id,
                *self._ownership_filters(),
            )
        )
        await self.session.commit()
        return rowcount(result) > 0

    async def interrupt_running(self) -> int:
        now = utc_now()
        result = await self.session.execute(
            update(AgentSession)
            .where(AgentSession.status == "running", *self._ownership_filters())
            .values(status="interrupted", updated_at=now)
        )
        await self.session.commit()
        return rowcount(result)
