"""Detect concurrent writes on the shared in-memory SQLite connection.

``sqlite+aiosqlite:///:memory:`` uses StaticPool; all sessions share one DBAPI connection. Their
transactions exist only in SQLAlchemy bookkeeping: one task's commit commits another's pending writes,
and rollback undoes them together; severe interleaving can deadlock. Cases requiring simultaneous
writes from multiple tasks must use file-database fixtures with independent connections.

A write is concurrent when another task has an uncommitted/unrolled-back write transaction on another
connection, including a finished task that left its transaction open. Raise ``SharedMemoryDbConcurrentWrite``
at the write; if broad business exception handling swallows it, engine teardown's ``raise_if_violated``
raises again. Nested sessions within the same task are not concurrent.
"""

from __future__ import annotations

import asyncio
import re
import weakref
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import AsyncEngine

_WRITE_STATEMENT = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


_GUARDS: weakref.WeakKeyDictionary[Engine, SharedConnectionWriteGuard] = weakref.WeakKeyDictionary()


class SharedMemoryDbConcurrentWrite(AssertionError):
    """Multiple tasks wrote concurrently through the shared in-memory database connection."""


def _current_task() -> asyncio.Task[Any] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@dataclass
class SharedConnectionWriteGuard:
    """Concurrent-write guard attached to an in-memory engine; ``file_fixture`` names the suggested file fixture on violation."""

    file_fixture: str
    violations: list[str] = field(default_factory=list)
    _dirty: dict[Connection, asyncio.Task[Any] | None] = field(default_factory=dict)

    def _message(self, writer: asyncio.Task[Any] | None, holder: asyncio.Task[Any] | None) -> str:
        writer_name = writer.get_name() if writer is not None else "<无事件循环>"
        holder_name = holder.get_name() if holder is not None else "<无事件循环>"
        return (
            f"内存 SQLite 夹具的所有 session 共用同一个连接，任务 {writer_name} 写入时，任务 {holder_name} "
            f"的写事务尚未结束：一方提交或回滚会连带另一方，交错时会互相卡住。"
            f"多个任务同时写库的用例改用 `{self.file_fixture}` 夹具（文件库，每个连接独立）。"
        )

    def before_execute(self, conn: Connection, statement: str, context: Any) -> None:
        is_write = bool(context is not None and (context.isinsert or context.isupdate or context.isdelete)) or bool(
            _WRITE_STATEMENT.match(statement)
        )
        if not is_write:
            return
        writer = _current_task()
        for other, holder in self._dirty.items():
            if other is conn or holder is writer or holder is None:
                continue
            message = self._message(writer, holder)
            self.violations.append(message)
            raise SharedMemoryDbConcurrentWrite(message)
        self._dirty[conn] = writer

    def end_transaction(self, conn: Connection) -> None:
        self._dirty.pop(conn, None)

    def take_violations(self) -> list[str]:
        """Drain recorded violations after a case asserts them, preventing duplicate reports at engine teardown."""
        taken, self.violations = self.violations, []
        return taken

    def raise_if_violated(self) -> None:
        if self.violations:
            raise SharedMemoryDbConcurrentWrite(self.violations[0])


def install_shared_connection_write_guard(engine: AsyncEngine, *, file_fixture: str) -> SharedConnectionWriteGuard:
    """Attach a concurrent-write guard to an in-memory engine and return it for teardown ``raise_if_violated``."""
    guard = SharedConnectionWriteGuard(file_fixture=file_fixture)
    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before_cursor_execute(conn, _cursor, statement, _parameters, context, _executemany):
        guard.before_execute(conn, statement, context)

    @event.listens_for(sync_engine, "commit")
    def _on_commit(conn):
        guard.end_transaction(conn)

    @event.listens_for(sync_engine, "rollback")
    def _on_rollback(conn):
        guard.end_transaction(conn)

    _GUARDS[sync_engine] = guard
    return guard


def guard_for(engine: AsyncEngine) -> SharedConnectionWriteGuard:
    """The guard attached to an in-memory fixture engine."""
    return _GUARDS[engine.sync_engine]
