"""Tests for SessionRepository."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from lib.db.base import Base
from lib.db.repositories.session_repo import SessionRepository


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestSessionRepository:
    async def test_create_and_get(self, db_session):
        repo = SessionRepository(db_session)
        created = await repo.create("demo", "sdk-001", "Test Session")
        assert created["project_name"] == "demo"
        assert created["status"] == "idle"
        assert created["title"] == "Test Session"
        assert created["sdk_session_id"] == "sdk-001"

        fetched = await repo.get("sdk-001")
        assert fetched is not None
        assert fetched["sdk_session_id"] == "sdk-001"

    async def test_list_with_filters(self, db_session):
        repo = SessionRepository(db_session)
        await repo.create("project_a", "sdk-a1", "Session A1")
        await repo.create("project_a", "sdk-a2", "Session A2")
        await repo.create("project_b", "sdk-b1", "Session B1")

        results = await repo.list(project_name="project_a")
        assert len(results) == 2

        results = await repo.list(project_name="project_b")
        assert len(results) == 1

    async def test_update_status(self, db_session):
        repo = SessionRepository(db_session)
        created = await repo.create("demo", "sdk-002", "Test")
        assert await repo.update_status("sdk-002", "running")

        fetched = await repo.get("sdk-002")
        assert fetched["status"] == "running"

    async def test_delete(self, db_session):
        repo = SessionRepository(db_session)
        await repo.create("demo", "sdk-003", "Test")
        deleted = await repo.delete("sdk-003")
        assert deleted
        assert await repo.get("sdk-003") is None

    async def test_delete_nonexistent(self, db_session):
        repo = SessionRepository(db_session)
        result = await repo.delete("nonexistent")
        assert not result

    async def test_interrupt_running(self, db_session):
        repo = SessionRepository(db_session)
        await repo.create("demo", "sdk-r1", "Running")
        await repo.create("demo", "sdk-r2", "Completed")
        await repo.create("demo", "sdk-r3", "Idle")

        await repo.update_status("sdk-r1", "running")
        await repo.update_status("sdk-r2", "completed")

        count = await repo.interrupt_running()
        assert count == 1

        assert (await repo.get("sdk-r1"))["status"] == "interrupted"
        assert (await repo.get("sdk-r2"))["status"] == "completed"
        assert (await repo.get("sdk-r3"))["status"] == "idle"
