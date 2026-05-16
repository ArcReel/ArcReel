"""DbSessionStore — SQLAlchemy-backed SDK SessionStore implementation."""

from __future__ import annotations

import asyncio
import logging
import random
import time

from claude_agent_sdk import fold_session_summary
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.agent_session_store.models import AgentSessionEntry, AgentSessionSummary
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

            # Maintain per-session summary for list_session_summaries fast path.
            # Per SDK protocol: skip for subagent transcripts (subpath != "").
            if subpath == "":
                await self._fold_summary_locked(session, project_key, session_id, entries, now_ms, now_dt)

            await session.commit()

        logger.info(
            "append: session=%s subpath=%s entries=%d seq_start=%d",
            session_id,
            subpath or "<main>",
            len(entries),
            seq_start,
        )

    async def _fold_summary_locked(
        self,
        session,
        project_key: str,
        session_id: str,
        entries: list[dict],
        now_ms: int,
        now_dt,
    ) -> None:
        """Read-fold-write the per-session summary inside the active transaction.

        Acquires a row lock on PG (SELECT ... FOR UPDATE) so concurrent appends
        can't lose folds. SQLite serializes writers via BEGIN IMMEDIATE.
        """
        bind = session.bind
        dialect = bind.dialect.name if bind is not None else "sqlite"

        stmt = select(AgentSessionSummary).where(
            AgentSessionSummary.project_key == project_key,
            AgentSessionSummary.session_id == session_id,
        )
        if dialect == "postgresql":
            stmt = stmt.with_for_update()
        prev_row = (await session.execute(stmt)).scalar_one_or_none()

        if prev_row is None:
            prev: dict | None = None
        else:
            prev = {
                "session_id": session_id,
                "mtime": int(prev_row.mtime_ms),
                "data": prev_row.data,
            }

        # SDK signature is (prev, key, entries) — fold returns mtime=0 placeholder
        # we overwrite with our own clock per SDK docstring guidance.
        key_for_fold = {"project_key": project_key, "session_id": session_id}
        folded = fold_session_summary(prev, key_for_fold, entries)  # type: ignore[arg-type]
        new_data = folded["data"] if folded else {}

        if prev_row is None:
            session.add(
                AgentSessionSummary(
                    project_key=project_key,
                    session_id=session_id,
                    mtime_ms=now_ms,
                    data=new_data,
                    user_id=self._user_id,
                    created_at=now_dt,
                    updated_at=now_dt,
                )
            )
        else:
            prev_row.mtime_ms = now_ms
            prev_row.data = new_data
            prev_row.updated_at = now_dt

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

    # --- optional: list_sessions / list_session_summaries -------------------

    async def list_sessions(self, project_key: str) -> list[dict]:
        async with self._session_factory() as session:
            stmt = (
                select(
                    AgentSessionEntry.session_id,
                    func.max(AgentSessionEntry.mtime_ms).label("mtime"),
                )
                .where(
                    AgentSessionEntry.project_key == project_key,
                    AgentSessionEntry.subpath == "",
                )
                .group_by(AgentSessionEntry.session_id)
            )
            result = await session.execute(stmt)
            return [{"session_id": r.session_id, "mtime": int(r.mtime)} for r in result.all()]

    async def list_session_summaries(self, project_key: str) -> list[dict]:
        async with self._session_factory() as session:
            stmt = select(AgentSessionSummary).where(
                AgentSessionSummary.project_key == project_key,
            )
            result = await session.execute(stmt)
            return [
                {"session_id": r.session_id, "mtime": int(r.mtime_ms), "data": r.data} for r in result.scalars().all()
            ]

    # --- optional: delete + list_subkeys -----------------------------------

    async def delete(self, key: dict) -> None:
        project_key, session_id, subpath = _normalize_key(key)
        async with self._session_factory() as session:
            entry_stmt = sa_delete(AgentSessionEntry).where(
                AgentSessionEntry.project_key == project_key,
                AgentSessionEntry.session_id == session_id,
            )
            if "subpath" in key and key["subpath"] != "":
                entry_stmt = entry_stmt.where(AgentSessionEntry.subpath == subpath)
            entry_result = await session.execute(entry_stmt)

            sum_rows = 0
            if subpath == "" and "subpath" not in key:
                # main delete cascades to summary
                sum_result = await session.execute(
                    sa_delete(AgentSessionSummary).where(
                        AgentSessionSummary.project_key == project_key,
                        AgentSessionSummary.session_id == session_id,
                    )
                )
                sum_rows = sum_result.rowcount or 0

            await session.commit()
        logger.info(
            "delete: session=%s subpath=%s entries=%d summaries=%d",
            session_id,
            subpath or "<main>",
            entry_result.rowcount or 0,
            sum_rows,
        )

    async def list_subkeys(self, key: dict) -> list[str]:
        project_key, session_id, _subpath = _normalize_key(key)
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentSessionEntry.subpath)
                .where(
                    AgentSessionEntry.project_key == project_key,
                    AgentSessionEntry.session_id == session_id,
                    AgentSessionEntry.subpath != "",
                )
                .distinct()
            )
            return [row[0] for row in result.all()]
