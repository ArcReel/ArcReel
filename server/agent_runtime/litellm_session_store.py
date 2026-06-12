"""LiteLLM session persistence — conversation history in DB.

Reuses the existing ``AgentSessionEntry`` table (same as Claude SDK store)
but with a different subpath convention to avoid collisions:
- Claude: ``subpath == ""`` (root transcript)
- LiteLLM: ``subpath == "litellm"`` (OpenAI message format)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.agent_session_store.store import DbSessionStore
from lib.db import safe_session_factory

logger = logging.getLogger(__name__)


class LiteLLMSessionStore:
    """Persist and retrieve LiteLLM conversation history.

    Messages are stored in OpenAI format (role/content/tool_calls/etc.)
    in the ``AgentSessionEntry`` table with ``subpath="litellm"``.
    """

    def __init__(self, session_factory=None):
        self._session_factory = session_factory or safe_session_factory

    async def save_messages(
        self,
        project_key: str,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        """Save conversation history for a LiteLLM session.

        Replaces all existing messages for this session (full overwrite).
        """
        from lib.db.models.session import AgentSessionEntry

        async with self._session_factory() as session:
            # Delete existing entries for this session
            await session.execute(
                AgentSessionEntry.__table__.delete().where(
                    AgentSessionEntry.project_key == project_key,
                    AgentSessionEntry.session_id == session_id,
                    AgentSessionEntry.subpath == "litellm",
                )
            )

            # Insert new entries
            for seq, msg in enumerate(messages):
                entry = AgentSessionEntry(
                    project_key=project_key,
                    session_id=session_id,
                    subpath="litellm",
                    seq=seq,
                    payload=msg,
                    mtime_ms=int(__import__("time").time() * 1000),
                )
                session.add(entry)

            await session.commit()
            logger.debug(
                "Saved %d messages for session %s (project=%s)",
                len(messages), session_id, project_key,
            )

    async def load_messages(
        self,
        project_key: str,
        session_id: str,
    ) -> list[dict[str, Any]] | None:
        """Load conversation history for a LiteLLM session.

        Returns ``None`` if no history exists.
        """
        from lib.db.models.session import AgentSessionEntry

        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentSessionEntry.payload)
                .where(
                    AgentSessionEntry.project_key == project_key,
                    AgentSessionEntry.session_id == session_id,
                    AgentSessionEntry.subpath == "litellm",
                )
                .order_by(AgentSessionEntry.seq)
            )
            payloads = [row[0] for row in result.all()]

        return payloads if payloads else None

    async def delete_session(
        self,
        project_key: str,
        session_id: str,
    ) -> None:
        """Delete all stored messages for a LiteLLM session."""
        from lib.db.models.session import AgentSessionEntry

        async with self._session_factory() as session:
            await session.execute(
                AgentSessionEntry.__table__.delete().where(
                    AgentSessionEntry.project_key == project_key,
                    AgentSessionEntry.session_id == session_id,
                    AgentSessionEntry.subpath == "litellm",
                )
            )
            await session.commit()
            logger.debug("Deleted LiteLLM session %s (project=%s)", session_id, project_key)

    async def list_sessions(
        self,
        project_key: str,
    ) -> list[dict[str, Any]]:
        """List all LiteLLM sessions for a project."""
        from lib.db.models.session import AgentSessionEntry
        from sqlalchemy import func

        async with self._session_factory() as session:
            stmt = (
                select(
                    AgentSessionEntry.session_id,
                    func.max(AgentSessionEntry.mtime_ms).label("mtime"),
                )
                .where(
                    AgentSessionEntry.project_key == project_key,
                    AgentSessionEntry.subpath == "litellm",
                )
                .group_by(AgentSessionEntry.session_id)
            )
            result = await session.execute(stmt)
            return [
                {"session_id": r.session_id, "mtime": int(r.mtime)}
                for r in result.all()
            ]
