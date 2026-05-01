"""DbSessionStore — SQLAlchemy-backed SDK SessionStore implementation."""

from __future__ import annotations

import asyncio
import logging
import random
import time

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.agent_session_store.models import AgentSessionEntry
from lib.db.base import DEFAULT_USER_ID, utc_now

logger = logging.getLogger("arcreel.session_store")

_MAX_APPEND_RETRY = 16
_APPEND_BACKOFF_CAP_S = 0.05


def _normalize_key(key: dict) -> tuple[str, str, str]:
    return key["project_key"], key["session_id"], key.get("subpath", "") or ""


def _entry_type(entry: dict) -> str:
    t = entry.get("type")
    return t if isinstance(t, str) else ""


def _entry_uuid(entry: dict) -> str | None:
    u = entry.get("uuid")
    return u if isinstance(u, str) and u else None


class DbSessionStore:
    """SDK SessionStore mirroring transcripts into the project database.

    Bind one instance per logical user — appends carry ``user_id`` for
    FK CASCADE on user deletion.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        user_id: str = DEFAULT_USER_ID,
    ) -> None:
        self._session_factory = session_factory
        self._user_id = user_id

    # --- required: append + load ---------------------------------------------

    async def append(self, key: dict, entries: list[dict]) -> None:
        if not entries:
            return
        project_key, session_id, subpath = _normalize_key(key)
        now_ms = int(time.time() * 1000)

        for attempt in range(_MAX_APPEND_RETRY):
            try:
                await self._append_once(project_key, session_id, subpath, entries, now_ms)
                return
            except IntegrityError as exc:
                # Narrow retry to the seq-PK race only. Both SQLite ("UNIQUE
                # constraint failed: ... seq") and PostgreSQL ("duplicate key
                # value violates unique constraint" with the seq column in the
                # detail) include these tokens for this specific PK collision;
                # other unique violations (e.g. uuid dedup) bubble up.
                msg = str(exc.orig) if exc.orig else str(exc)
                is_seq_race = "seq" in msg and ("UNIQUE" in msg or "duplicate key" in msg)
                if not is_seq_race:
                    raise
                if attempt == _MAX_APPEND_RETRY - 1:
                    logger.error(
                        "append: PK conflict after %d attempts session=%s subpath=%s entries=%d",
                        _MAX_APPEND_RETRY,
                        session_id,
                        subpath or "<main>",
                        len(entries),
                    )
                    raise
                logger.warning(
                    "append: seq race retry=%d session=%s subpath=%s err=%s",
                    attempt + 1,
                    session_id,
                    subpath or "<main>",
                    exc,
                )
                # Jittered exponential backoff capped at ~50ms — keeps SQLite's
                # writer-lock contention from amplifying under high concurrency
                # while staying well below the busy_timeout.
                delay = random.uniform(0, min(_APPEND_BACKOFF_CAP_S, 0.001 * (2**attempt)))
                await asyncio.sleep(delay)

    async def _append_once(
        self,
        project_key: str,
        session_id: str,
        subpath: str,
        entries: list[dict],
        now_ms: int,
    ) -> None:
        now_dt = utc_now()
        async with self._session_factory() as session:
            seq_start_row = await session.execute(
                select(func.coalesce(func.max(AgentSessionEntry.seq), -1) + 1).where(
                    AgentSessionEntry.project_key == project_key,
                    AgentSessionEntry.session_id == session_id,
                    AgentSessionEntry.subpath == subpath,
                )
            )
            seq_start = int(seq_start_row.scalar_one())

            rows = [
                {
                    "project_key": project_key,
                    "session_id": session_id,
                    "subpath": subpath,
                    "seq": seq_start + i,
                    "uuid": _entry_uuid(entry),
                    "entry_type": _entry_type(entry),
                    "payload": entry,
                    "mtime_ms": now_ms,
                    "user_id": self._user_id,
                    "created_at": now_dt,
                    "updated_at": now_dt,
                }
                for i, entry in enumerate(entries)
            ]

            await self._insert_entries(session, rows)
            await session.commit()

        logger.info(
            "append: session=%s subpath=%s entries=%d seq_start=%d",
            session_id,
            subpath or "<main>",
            len(entries),
            seq_start,
        )

    async def _insert_entries(self, session, rows: list[dict]) -> None:
        """Dialect-aware INSERT ... ON CONFLICT (uuid) DO NOTHING.

        Targets the partial unique index ``uq_agent_entries_uuid`` (WHERE
        uuid IS NOT NULL); both PG and SQLite require ``index_where`` to
        match a partial index inference target.
        """
        bind = session.bind
        dialect = bind.dialect.name if bind is not None else "sqlite"
        index_elements = ["project_key", "session_id", "subpath", "uuid"]
        index_where = text("uuid IS NOT NULL")

        if dialect == "postgresql":
            stmt = pg_insert(AgentSessionEntry).values(rows)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=index_elements,
                index_where=index_where,
            )
        else:
            stmt = sqlite_insert(AgentSessionEntry).values(rows)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=index_elements,
                index_where=index_where,
            )
        await session.execute(stmt)

    async def load(self, key: dict) -> list[dict] | None:
        project_key, session_id, subpath = _normalize_key(key)
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentSessionEntry.payload)
                .where(
                    AgentSessionEntry.project_key == project_key,
                    AgentSessionEntry.session_id == session_id,
                    AgentSessionEntry.subpath == subpath,
                )
                .order_by(AgentSessionEntry.seq)
            )
            payloads = [row[0] for row in result.all()]
        if not payloads:
            return None
        return payloads
