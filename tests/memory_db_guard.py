"""内存 SQLite 共享连接上的并发写检查。

``sqlite+aiosqlite:///:memory:`` 走 StaticPool，所有 session 共用同一个 DBAPI 连接，各自的
「事务」只是 SQLAlchemy 层的记账：一个任务提交会把另一个任务未提交的写一并提交，回滚则一并
撤销，交错严重时互相卡住。需要多个任务同时写库的用例应改用文件库夹具，每个连接独立。

检查规则：某个任务在一个连接上执行写语句时，若另一个任务在别的连接上开启的写事务尚未提交
或回滚，就判为并发写；发起写事务的任务已经结束、事务却留着没收尾的同样算。违规在写语句处
直接抛 ``SharedMemoryDbConcurrentWrite``；被业务代码的宽泛异常捕获吞掉时，engine 关闭时
``raise_if_violated`` 再抛一次。同一任务里嵌套打开的 session 不算并发。
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
    """内存库共享连接上出现了多个任务的并发写。"""


def _current_task() -> asyncio.Task[Any] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@dataclass
class SharedConnectionWriteGuard:
    """挂在内存库 engine 上的并发写检查；``file_fixture`` 是违规时建议改用的文件库夹具名。"""

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
        """取走已记录的违规；用例自己断言过违规时调用，engine 关闭时不再重复报告。"""
        taken, self.violations = self.violations, []
        return taken

    def raise_if_violated(self) -> None:
        if self.violations:
            raise SharedMemoryDbConcurrentWrite(self.violations[0])


def install_shared_connection_write_guard(engine: AsyncEngine, *, file_fixture: str) -> SharedConnectionWriteGuard:
    """给内存库 engine 挂上并发写检查，返回检查器供关闭时 ``raise_if_violated``。"""
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
    """内存库夹具 engine 上挂的检查器。"""
    return _GUARDS[engine.sync_engine]
