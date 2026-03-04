#!/usr/bin/env python3
"""One-time migration script: copy data from old hand-written SQLite DBs to new ORM DB.

Old files:
  projects/.task_queue.db       → tasks, task_events, worker_lease tables
  projects/.api_usage.db        → api_calls table
  projects/.agent_data/sessions.db → agent_sessions table

New file:
  projects/.arcreel.db          (created by init_db / Alembic)

On success, old files are renamed to *.bak so they are preserved but won't
interfere with the new code.

Usage:
    python scripts/migrate_sqlite_to_orm.py [--dry-run]
"""

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from lib.db import init_db  # noqa: E402
from lib.db.engine import async_session_factory  # noqa: E402
from lib.db.models import AgentSession, ApiCall, Task, TaskEvent, WorkerLease  # noqa: E402

PROJECTS_DIR = ROOT / "projects"
OLD_TASK_DB = PROJECTS_DIR / ".task_queue.db"
OLD_USAGE_DB = PROJECTS_DIR / ".api_usage.db"
OLD_SESSIONS_DB = PROJECTS_DIR / ".agent_data" / "sessions.db"


def _read_old_tasks(conn: sqlite3.Connection) -> tuple[list, list, list]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM tasks")
    tasks = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM task_events")
    events = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM worker_lease")
    leases = [dict(r) for r in cur.fetchall()]

    return tasks, events, leases


def _read_old_usage(conn: sqlite3.Connection) -> list:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM api_calls")
    return [dict(r) for r in cur.fetchall()]


def _read_old_sessions(conn: sqlite3.Connection) -> list:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM sessions")
    return [dict(r) for r in cur.fetchall()]


async def _migrate(dry_run: bool) -> dict[str, int]:
    stats: dict[str, int] = {}

    # --- Read old data ---
    tasks, events, leases = [], [], []
    if OLD_TASK_DB.exists():
        with sqlite3.connect(OLD_TASK_DB) as conn:
            tasks, events, leases = _read_old_tasks(conn)
        print(f"  读取旧任务队列: tasks={len(tasks)}, events={len(events)}, leases={len(leases)}")
    else:
        print(f"  跳过（不存在）: {OLD_TASK_DB}")

    api_calls = []
    if OLD_USAGE_DB.exists():
        with sqlite3.connect(OLD_USAGE_DB) as conn:
            api_calls = _read_old_usage(conn)
        print(f"  读取旧 API 用量: api_calls={len(api_calls)}")
    else:
        print(f"  跳过（不存在）: {OLD_USAGE_DB}")

    sessions = []
    if OLD_SESSIONS_DB.exists():
        with sqlite3.connect(OLD_SESSIONS_DB) as conn:
            sessions = _read_old_sessions(conn)
        print(f"  读取旧会话记录: sessions={len(sessions)}")
    else:
        print(f"  跳过（不存在）: {OLD_SESSIONS_DB}")

    if dry_run:
        print("\n[DRY RUN] 不写入数据库，不重命名旧文件。")
        return {"tasks": len(tasks), "events": len(events), "leases": len(leases),
                "api_calls": len(api_calls), "sessions": len(sessions)}

    # --- Ensure new DB tables exist ---
    await init_db()

    async with async_session_factory() as session:
        # Tasks
        for row in tasks:
            session.add(Task(
                task_id=row["task_id"],
                project_name=row["project_name"],
                task_type=row["task_type"],
                media_type=row["media_type"],
                resource_id=row["resource_id"],
                script_file=row.get("script_file"),
                payload_json=row.get("payload_json"),
                status=row["status"],
                result_json=row.get("result_json"),
                error_message=row.get("error_message"),
                source=row.get("source", "webui"),
                dependency_task_id=row.get("dependency_task_id"),
                dependency_group=row.get("dependency_group"),
                dependency_index=row.get("dependency_index"),
                queued_at=row["queued_at"],
                started_at=row.get("started_at"),
                finished_at=row.get("finished_at"),
                updated_at=row["updated_at"],
            ))

        # Task events
        for row in events:
            session.add(TaskEvent(
                task_id=row["task_id"],
                project_name=row["project_name"],
                event_type=row["event_type"],
                status=row["status"],
                data_json=row.get("data_json"),
                created_at=row["created_at"],
            ))

        # Worker leases
        for row in leases:
            session.add(WorkerLease(
                name=row["name"],
                owner_id=row["owner_id"],
                lease_until=row["lease_until"],
                updated_at=row["updated_at"],
            ))

        # API calls
        for row in api_calls:
            session.add(ApiCall(
                project_name=row["project_name"],
                call_type=row["call_type"],
                model=row["model"],
                prompt=row.get("prompt"),
                resolution=row.get("resolution"),
                duration_seconds=row.get("duration_seconds"),
                aspect_ratio=row.get("aspect_ratio"),
                generate_audio=row.get("generate_audio"),
                status=row.get("status", "pending"),
                error_message=row.get("error_message"),
                output_path=row.get("output_path"),
                started_at=row["started_at"],
                finished_at=row.get("finished_at"),
                duration_ms=row.get("duration_ms"),
                retry_count=row.get("retry_count", 0),
                cost_usd=row.get("cost_usd", 0.0),
                created_at=row.get("created_at"),
            ))

        # Agent sessions
        for row in sessions:
            session.add(AgentSession(
                id=row["id"],
                sdk_session_id=row.get("sdk_session_id"),
                project_name=row["project_name"],
                title=row.get("title", ""),
                status=row.get("status", "idle"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            ))

        await session.commit()

    stats = {
        "tasks": len(tasks),
        "task_events": len(events),
        "worker_leases": len(leases),
        "api_calls": len(api_calls),
        "agent_sessions": len(sessions),
    }

    # --- Rename old files to .bak ---
    for old_path in [OLD_TASK_DB, OLD_USAGE_DB, OLD_SESSIONS_DB]:
        if old_path.exists():
            bak_path = old_path.with_suffix(".db.bak")
            old_path.rename(bak_path)
            print(f"  已重命名: {old_path.name} → {bak_path.name}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Migrate old SQLite DBs to new ORM DB")
    parser.add_argument("--dry-run", action="store_true", help="只读取，不写入")
    args = parser.parse_args()

    print("=== SQLite → ORM 数据迁移 ===\n")
    print(f"模式: {'DRY RUN' if args.dry_run else '实际迁移'}\n")

    stats = asyncio.run(_migrate(args.dry_run))

    print("\n=== 迁移统计 ===")
    for key, count in stats.items():
        print(f"  {key}: {count} 条")

    print("\n迁移完成！")


if __name__ == "__main__":
    main()
