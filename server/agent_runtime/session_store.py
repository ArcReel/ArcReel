"""
SQLite-based session metadata storage.
"""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from server.agent_runtime.models import SessionMeta, SessionStatus


class SessionMetaStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_session(row: sqlite3.Row) -> SessionMeta:
        return SessionMeta(
            id=row["id"],
            sdk_session_id=row["sdk_session_id"],
            project_name=row["project_name"],
            title=row["title"] or "",
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    sdk_session_id TEXT,
                    project_name TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    status TEXT DEFAULT 'idle',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_project
                ON sessions (project_name, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_status
                ON sessions (status)
                """
            )
            # 迁移：删除废弃的 transcript_path 列
            columns = [
                row[1]
                for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
            ]
            if "transcript_path" in columns:
                conn.execute("ALTER TABLE sessions DROP COLUMN transcript_path")

    def create(self, project_name: str, title: str = "") -> SessionMeta:
        session_id = uuid.uuid4().hex
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, project_name, title, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, project_name, title, "idle", now, now),
            )
        session = self.get(session_id)
        if session is None:
            raise RuntimeError("failed to create session")
        return session

    def get(self, session_id: str) -> Optional[SessionMeta]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, sdk_session_id, project_name, title, status, created_at, updated_at
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return self._to_session(row)

    def list(
        self,
        project_name: Optional[str] = None,
        status: Optional[SessionStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionMeta]:
        clauses: list[str] = []
        params: list[object] = []

        if project_name:
            clauses.append("project_name = ?")
            params.append(project_name)
        if status:
            clauses.append("status = ?")
            params.append(status)

        query = "SELECT id, sdk_session_id, project_name, title, status, created_at, updated_at FROM sessions"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([max(1, limit), max(0, offset)])

        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._to_session(row) for row in rows]

    def update_status(self, session_id: str, status: SessionStatus) -> bool:
        now = self._now()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, session_id),
            )
        return cursor.rowcount > 0

    def update_sdk_session_id(self, session_id: str, sdk_session_id: str) -> bool:
        now = self._now()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET sdk_session_id = ?, updated_at = ? WHERE id = ?",
                (sdk_session_id, now, session_id),
            )
        return cursor.rowcount > 0

    def update_title(self, session_id: str, title: str) -> bool:
        now = self._now()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title.strip(), now, session_id),
            )
        return cursor.rowcount > 0

    def delete(self, session_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cursor.rowcount > 0
