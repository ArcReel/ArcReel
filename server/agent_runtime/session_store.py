"""
Async session metadata storage.

Wraps SessionRepository with a convenience class.
"""

from __future__ import annotations

from typing import Optional

from lib.db import safe_session_factory
from lib.db.repositories.session_repo import SessionRepository
from server.agent_runtime.models import SessionMeta, SessionStatus


def _dict_to_session(d: dict) -> SessionMeta:
    """Convert a repository dict to a SessionMeta dataclass."""
    return SessionMeta(
        id=d["sdk_session_id"],  # DB 内部 id 不暴露，对外统一用 sdk_session_id
        project_name=d["project_name"],
        title=d.get("title") or "",
        status=d["status"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


class SessionMetaStore:
    """Async session metadata store wrapping SessionRepository."""

    def __init__(self, *, session_factory=None):
        self._session_factory = session_factory or safe_session_factory

    async def create(self, project_name: str, sdk_session_id: str) -> SessionMeta:

        async with self._session_factory() as session:
            repo = SessionRepository(session)
            d = await repo.create(project_name=project_name, sdk_session_id=sdk_session_id)
        return _dict_to_session(d)

    async def get(self, session_id: str) -> Optional[SessionMeta]:

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

        async with self._session_factory() as session:
            repo = SessionRepository(session)
            return await repo.update_status(session_id, status)

    async def interrupt_running_sessions(self) -> int:

        async with self._session_factory() as session:
            repo = SessionRepository(session)
            return await repo.interrupt_running()

    async def delete(self, session_id: str) -> bool:

        async with self._session_factory() as session:
            repo = SessionRepository(session)
            return await repo.delete(session_id)
