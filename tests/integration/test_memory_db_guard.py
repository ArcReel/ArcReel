"""内存库共享连接并发写检查（`tests/memory_db_guard`）的契约。

内存库夹具上，一个任务的写事务未结束时另一个任务写库，写语句当场失败并指向对应的文件库夹具；
失败被宽泛异常捕获吞掉时，engine 关闭时照样报告。先后完成的写、同一任务里嵌套的 session
不算并发。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.memory_db_guard import SharedMemoryDbConcurrentWrite, guard_for


async def _create_probe_table(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await session.execute(text("CREATE TABLE probe (v TEXT)"))
        await session.commit()


async def _insert(session: AsyncSession, value: str) -> None:
    await session.execute(text("INSERT INTO probe (v) VALUES (:v)"), {"v": value})


async def _write_while_other_task_holds_open_write(factory: async_sessionmaker[AsyncSession]) -> BaseException | None:
    """任务 A 写入后挂起不提交，任务 B 此时写入；返回 B 的写入异常。"""
    a_wrote = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with factory() as session:
            await _insert(session, "a")
            a_wrote.set()
            await release.wait()
            await session.commit()

    async def writer() -> BaseException | None:
        await a_wrote.wait()
        try:
            async with factory() as session:
                await _insert(session, "b")
                await session.commit()
        except BaseException as exc:
            return exc
        finally:
            release.set()
        return None

    _, error = await asyncio.gather(holder(), writer())
    return error


async def test_concurrent_write_fails_at_once_and_points_to_file_fixture(
    db_engine: AsyncEngine, db_factory: async_sessionmaker[AsyncSession]
):
    await _create_probe_table(db_factory)

    error = await _write_while_other_task_holds_open_write(db_factory)

    assert isinstance(error, SharedMemoryDbConcurrentWrite)
    assert "`file_db_factory`" in str(error)
    assert len(guard_for(db_engine).take_violations()) == 1


async def test_swallowed_concurrent_write_is_still_reported(
    db_engine: AsyncEngine, db_factory: async_sessionmaker[AsyncSession]
):
    await _create_probe_table(db_factory)
    await _write_while_other_task_holds_open_write(db_factory)
    guard = guard_for(db_engine)

    with pytest.raises(SharedMemoryDbConcurrentWrite, match="`file_db_factory`"):
        guard.raise_if_violated()
    guard.take_violations()


@pytest.mark.sqlite_only
async def test_dialect_aware_fixture_points_to_concurrent_session_factory(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _create_probe_table(session_factory)

    error = await _write_while_other_task_holds_open_write(session_factory)

    assert isinstance(error, SharedMemoryDbConcurrentWrite)
    assert "`concurrent_session_factory`" in str(error)
    engine = session_factory.kw["bind"]
    guard_for(engine).take_violations()


async def test_writes_finished_one_after_another_pass(
    db_engine: AsyncEngine, db_factory: async_sessionmaker[AsyncSession]
):
    await _create_probe_table(db_factory)

    async def write(value: str) -> None:
        async with db_factory() as session:
            await _insert(session, value)
            await session.commit()

    await asyncio.create_task(write("a"))
    await asyncio.create_task(write("b"))

    async with db_factory() as session:
        rows = (await session.execute(text("SELECT v FROM probe ORDER BY v"))).scalars().all()
    assert rows == ["a", "b"]
    assert guard_for(db_engine).violations == []


async def test_nested_sessions_in_one_task_pass(db_engine: AsyncEngine, db_factory: async_sessionmaker[AsyncSession]):
    await _create_probe_table(db_factory)

    async with db_factory() as outer:
        await _insert(outer, "outer")
        async with db_factory() as inner:
            await _insert(inner, "inner")
            await inner.commit()
        await outer.commit()

    assert guard_for(db_engine).violations == []
