"""
SQLite-backed generation task queue shared by WebUI and skills.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ACTIVE_TASK_STATUSES = ("queued", "running")
TERMINAL_TASK_STATUSES = ("succeeded", "failed")
TASK_QUEUE_DB_RELATIVE_PATH = "projects/.task_queue.db"
TASK_WORKER_LEASE_TTL_SEC = 10.0
TASK_WORKER_HEARTBEAT_SEC = 3.0
TASK_SSE_HEARTBEAT_SEC = 15.0
TASK_POLL_INTERVAL_SEC = 1.0


_QUEUE_LOCK = threading.Lock()
_QUEUE_INSTANCE: Optional["GenerationQueue"] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def resolve_queue_db_path() -> Path:
    """Resolve queue DB path (relative to project root)."""
    db_path = Path(TASK_QUEUE_DB_RELATIVE_PATH)
    if db_path.is_absolute():
        return db_path
    project_root = Path(__file__).parent.parent
    return project_root / db_path


class GenerationQueue:
    """Persistent queue manager built on SQLite."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else resolve_queue_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=OFF")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    script_file TEXT,
                    payload_json TEXT,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error_message TEXT,
                    source TEXT NOT NULL DEFAULT 'webui',
                    queued_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_lease (
                    name TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    lease_until REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_status_queued_at ON tasks(status, queued_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_project_updated_at ON tasks(project_name, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_events_id ON task_events(id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_events_project_id ON task_events(project_name, id)"
            )
            # Migrate dedupe key to include script_file so same segment_id across episodes
            # is not treated as one active task.
            conn.execute("DROP INDEX IF EXISTS idx_tasks_dedupe_active")
            conn.execute(
                """
                CREATE UNIQUE INDEX idx_tasks_dedupe_active
                ON tasks(project_name, task_type, resource_id, COALESCE(script_file, ''))
                WHERE status IN ('queued', 'running')
                """
            )

    @staticmethod
    def _row_to_task_dict(row: sqlite3.Row) -> Dict[str, Any]:
        task = dict(row)
        task["payload"] = _json_loads(task.pop("payload_json", None), {})
        task["result"] = _json_loads(task.pop("result_json", None), None)
        return task

    @staticmethod
    def _row_to_event_dict(row: sqlite3.Row) -> Dict[str, Any]:
        event = dict(row)
        event["data"] = _json_loads(event.pop("data_json", None), {})
        return event

    def _append_event_conn(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str,
        project_name: str,
        event_type: str,
        status: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> int:
        created_at = _utc_now_iso()
        cursor = conn.execute(
            """
            INSERT INTO task_events(task_id, project_name, event_type, status, data_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                project_name,
                event_type,
                status,
                _json_dumps(data or {}),
                created_at,
            ),
        )
        return int(cursor.lastrowid)

    def enqueue_task(
        self,
        *,
        project_name: str,
        task_type: str,
        media_type: str,
        resource_id: str,
        payload: Optional[Dict[str, Any]] = None,
        script_file: Optional[str] = None,
        source: str = "webui",
    ) -> Dict[str, Any]:
        now = _utc_now_iso()
        task_id = uuid.uuid4().hex
        payload_json = _json_dumps(payload or {})

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO tasks(
                        task_id, project_name, task_type, media_type, resource_id,
                        script_file, payload_json, status, source,
                        queued_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                    """,
                    (
                        task_id,
                        project_name,
                        task_type,
                        media_type,
                        resource_id,
                        script_file,
                        payload_json,
                        source,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    """
                    SELECT task_id, status
                    FROM tasks
                    WHERE project_name = ?
                      AND task_type = ?
                      AND resource_id = ?
                      AND COALESCE(script_file, '') = COALESCE(?, '')
                      AND status IN ('queued', 'running')
                    ORDER BY queued_at DESC
                    LIMIT 1
                    """,
                    (project_name, task_type, resource_id, script_file),
                ).fetchone()
                conn.execute("COMMIT")
                if not existing:
                    raise
                return {
                    "task_id": existing["task_id"],
                    "status": existing["status"],
                    "deduped": True,
                    "existing_task_id": existing["task_id"],
                }

            task_row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            task_data = self._row_to_task_dict(task_row)
            self._append_event_conn(
                conn,
                task_id=task_id,
                project_name=project_name,
                event_type="queued",
                status="queued",
                data=task_data,
            )
            conn.execute("COMMIT")

        return {
            "task_id": task_id,
            "status": "queued",
            "deduped": False,
            "existing_task_id": None,
        }

    def claim_next_task(self, media_type: str) -> Optional[Dict[str, Any]]:
        now = _utc_now_iso()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM tasks
                WHERE status = 'queued'
                  AND media_type = ?
                ORDER BY queued_at ASC
                LIMIT 1
                """,
                (media_type,),
            ).fetchone()

            if not row:
                conn.execute("COMMIT")
                return None

            task_id = row["task_id"]
            conn.execute(
                """
                UPDATE tasks
                SET status = 'running',
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE task_id = ?
                """,
                (now, now, task_id),
            )

            running_row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            running_task = self._row_to_task_dict(running_row)
            self._append_event_conn(
                conn,
                task_id=task_id,
                project_name=running_task["project_name"],
                event_type="running",
                status="running",
                data=running_task,
            )
            conn.execute("COMMIT")
            return running_task

    def requeue_running_tasks(self, *, limit: int = 1000) -> int:
        """Requeue tasks stuck in running state (e.g. previous worker crashed)."""
        limit = max(1, min(5000, int(limit)))
        now = _utc_now_iso()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT task_id
                FROM tasks
                WHERE status = 'running'
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            recovered = 0
            for row in rows:
                task_id = row["task_id"]
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'queued',
                        started_at = NULL,
                        finished_at = NULL,
                        updated_at = ?,
                        result_json = NULL,
                        error_message = NULL
                    WHERE task_id = ?
                      AND status = 'running'
                    """,
                    (now, task_id),
                )

                task_row = conn.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if not task_row or task_row["status"] != "queued":
                    continue

                task_data = self._row_to_task_dict(task_row)
                self._append_event_conn(
                    conn,
                    task_id=task_id,
                    project_name=task_data["project_name"],
                    event_type="requeued",
                    status="queued",
                    data=task_data,
                )
                recovered += 1

            conn.execute("COMMIT")
            return recovered

    def mark_task_succeeded(self, task_id: str, result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        now = _utc_now_iso()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None

            conn.execute(
                """
                UPDATE tasks
                SET status = 'succeeded',
                    result_json = ?,
                    error_message = NULL,
                    finished_at = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (_json_dumps(result or {}), now, now, task_id),
            )

            done_row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            done_task = self._row_to_task_dict(done_row)
            self._append_event_conn(
                conn,
                task_id=task_id,
                project_name=done_task["project_name"],
                event_type="succeeded",
                status="succeeded",
                data=done_task,
            )
            conn.execute("COMMIT")
            return done_task

    def mark_task_failed(self, task_id: str, error_message: str) -> Optional[Dict[str, Any]]:
        now = _utc_now_iso()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None

            conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    error_message = ?,
                    finished_at = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (error_message[:2000], now, now, task_id),
            )

            failed_row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            failed_task = self._row_to_task_dict(failed_row)
            self._append_event_conn(
                conn,
                task_id=task_id,
                project_name=failed_task["project_name"],
                event_type="failed",
                status="failed",
                data=failed_task,
            )
            conn.execute("COMMIT")
            return failed_task

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_task_dict(row)

    def list_tasks(
        self,
        *,
        project_name: Optional[str] = None,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        source: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(500, int(page_size)))
        offset = (page - 1) * page_size

        conditions: List[str] = []
        params: List[Any] = []

        if project_name:
            conditions.append("project_name = ?")
            params.append(project_name)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if task_type:
            conditions.append("task_type = ?")
            params.append(task_type)
        if source:
            conditions.append("source = ?")
            params.append(source)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        with self._connect() as conn:
            count_row = conn.execute(
                f"SELECT COUNT(*) AS total FROM tasks {where_clause}", params
            ).fetchone()
            total = int(count_row["total"]) if count_row else 0

            rows = conn.execute(
                f"""
                SELECT *
                FROM tasks
                {where_clause}
                ORDER BY updated_at DESC, queued_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            ).fetchall()

        items = [self._row_to_task_dict(row) for row in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get_task_stats(self, project_name: Optional[str] = None) -> Dict[str, int]:
        conditions: List[str] = []
        params: List[Any] = []
        if project_name:
            conditions.append("project_name = ?")
            params.append(project_name)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT status, COUNT(*) AS cnt FROM tasks {where_clause} GROUP BY status",
                params,
            ).fetchall()
            total_row = conn.execute(
                f"SELECT COUNT(*) AS total FROM tasks {where_clause}",
                params,
            ).fetchone()

        stats = {
            "queued": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
            "total": int(total_row["total"]) if total_row else 0,
        }
        for row in rows:
            status = row["status"]
            if status in stats:
                stats[status] = int(row["cnt"])
        return stats

    def get_recent_tasks_snapshot(
        self,
        *,
        project_name: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(1000, int(limit)))
        where_clause = ""
        params: List[Any] = []
        if project_name:
            where_clause = "WHERE project_name = ?"
            params.append(project_name)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM tasks
                {where_clause}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        return [self._row_to_task_dict(row) for row in rows]

    def get_events_since(
        self,
        *,
        last_event_id: int,
        project_name: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(1000, int(limit)))
        params: List[Any] = [int(last_event_id)]

        where_clause = "WHERE id > ?"
        if project_name:
            where_clause += " AND project_name = ?"
            params.append(project_name)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM task_events
                {where_clause}
                ORDER BY id ASC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        return [self._row_to_event_dict(row) for row in rows]

    def get_latest_event_id(self, *, project_name: Optional[str] = None) -> int:
        params: List[Any] = []
        where_clause = ""
        if project_name:
            where_clause = "WHERE project_name = ?"
            params.append(project_name)

        with self._connect() as conn:
            row = conn.execute(
                f"SELECT MAX(id) AS max_id FROM task_events {where_clause}",
                params,
            ).fetchone()

        if not row:
            return 0
        return int(row["max_id"] or 0)

    def acquire_or_renew_worker_lease(
        self,
        *,
        name: str,
        owner_id: str,
        ttl_seconds: float,
    ) -> bool:
        now_epoch = time.time()
        lease_until = now_epoch + max(1.0, float(ttl_seconds))
        updated_at = _utc_now_iso()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner_id, lease_until FROM worker_lease WHERE name = ?",
                (name,),
            ).fetchone()

            if not row:
                conn.execute(
                    """
                    INSERT INTO worker_lease(name, owner_id, lease_until, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name, owner_id, lease_until, updated_at),
                )
                conn.execute("COMMIT")
                return True

            lease_owner = row["owner_id"]
            lease_expired = float(row["lease_until"]) <= now_epoch

            if lease_owner == owner_id or lease_expired:
                conn.execute(
                    """
                    UPDATE worker_lease
                    SET owner_id = ?, lease_until = ?, updated_at = ?
                    WHERE name = ?
                    """,
                    (owner_id, lease_until, updated_at, name),
                )
                conn.execute("COMMIT")
                return True

            conn.execute("COMMIT")
            return False

    def release_worker_lease(self, *, name: str, owner_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM worker_lease WHERE name = ? AND owner_id = ?",
                (name, owner_id),
            )

    def is_worker_online(self, *, name: str = "default") -> bool:
        now_epoch = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT lease_until FROM worker_lease WHERE name = ?",
                (name,),
            ).fetchone()
            if not row:
                return False
            return float(row["lease_until"]) > now_epoch

    def get_worker_lease(self, *, name: str = "default") -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, owner_id, lease_until, updated_at FROM worker_lease WHERE name = ?",
                (name,),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            data["is_online"] = float(data["lease_until"]) > time.time()
            return data


def get_generation_queue() -> GenerationQueue:
    global _QUEUE_INSTANCE
    if _QUEUE_INSTANCE is not None:
        return _QUEUE_INSTANCE

    with _QUEUE_LOCK:
        if _QUEUE_INSTANCE is None:
            _QUEUE_INSTANCE = GenerationQueue()
        return _QUEUE_INSTANCE


def read_queue_poll_interval() -> float:
    return max(0.1, float(TASK_POLL_INTERVAL_SEC))
