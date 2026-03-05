"""
Async session metadata storage.

Wraps SessionRepository with a convenience class.
"""

from __future__ import annotations

from typing import Optional

from lib.db import init_db, safe_session_factory
from lib.db.repositories.session_repo import SessionRepository
from server.agent_runtime.models import SessionMeta, SessionStatus


def _dict_to_session(d: dict) -> SessionMeta:
    """Convert a repository dict to a SessionMeta dataclass."""
    return SessionMeta(
        id=d["id"],
        sdk_session_id=d.get("sdk_session_id"),
        project_name=d["project_name"],
        title=d.get("title") or "",
        status=d["status"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


class SessionMetaStore:
    """Async session metadata store wrapping SessionRepository."""

    def __init__(self, *, session_factory=None, _skip_init_db: bool = False):
        self._session_factory = session_factory or safe_session_factory
        self._skip_init_db = _skip_init_db
        self._db_initialized = _skip_init_db

    async def _ensure_db(self) -> None:
        if not self._db_initialized:
            await init_db()
            self._db_initialized = True

    async def create(self, project_name: str, title: str = "") -> SessionMeta:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = SessionRepository(session)
            d = await repo.create(project_name=project_name, title=title)
        return _dict_to_session(d)

    async def get(self, session_id: str) -> Optional[SessionMeta]:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = SessionRepository(session)
            d = await repo.get(session_id)
        if d is None:
            return None
        return _dict_to_session(d)

    async def list(
        self,
        project_name: Optional[str] = None,
        status: Optional[SessionStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionMeta]:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = SessionRepository(session)
            result = await repo.list(
                project_name=project_name,
                status=status,
                limit=limit,
                offset=offset,
            )
        return [_dict_to_session(d) for d in result]

    async def update_status(self, session_id: str, status: SessionStatus) -> bool:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = SessionRepository(session)
            return await repo.update_status(session_id, status)

    async def interrupt_running_sessions(self) -> int:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = SessionRepository(session)
            return await repo.interrupt_running()

    async def update_sdk_session_id(self, session_id: str, sdk_session_id: str) -> bool:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = SessionRepository(session)
            return await repo.update_sdk_session_id(session_id, sdk_session_id)

    async def update_title(self, session_id: str, title: str) -> bool:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = SessionRepository(session)
            return await repo.update_title(session_id, title)

    async def delete(self, session_id: str) -> bool:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = SessionRepository(session)
            return await repo.delete(session_id)
