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
        created = await repo.create("demo", "Test Session")
        assert created["project_name"] == "demo"
        assert created["status"] == "idle"
        assert created["title"] == "Test Session"

        fetched = await repo.get(created["id"])
        assert fetched is not None
        assert fetched["id"] == created["id"]

    async def test_list_with_filters(self, db_session):
        repo = SessionRepository(db_session)
        await repo.create("project_a", "Session A1")
        await repo.create("project_a", "Session A2")
        await repo.create("project_b", "Session B1")

        results = await repo.list(project_name="project_a")
        assert len(results) == 2

        results = await repo.list(project_name="project_b")
        assert len(results) == 1

    async def test_update_status(self, db_session):
        repo = SessionRepository(db_session)
        created = await repo.create("demo", "Test")
        assert await repo.update_status(created["id"], "running")

        fetched = await repo.get(created["id"])
        assert fetched["status"] == "running"

    async def test_update_title(self, db_session):
        repo = SessionRepository(db_session)
        created = await repo.create("demo", "Original")
        assert await repo.update_title(created["id"], "Renamed")

        fetched = await repo.get(created["id"])
        assert fetched["title"] == "Renamed"

    async def test_update_sdk_session_id(self, db_session):
        repo = SessionRepository(db_session)
        created = await repo.create("demo", "Test")
        assert await repo.update_sdk_session_id(created["id"], "sdk-abc")

        fetched = await repo.get(created["id"])
        assert fetched["sdk_session_id"] == "sdk-abc"

    async def test_delete(self, db_session):
        repo = SessionRepository(db_session)
        created = await repo.create("demo", "Test")
        deleted = await repo.delete(created["id"])
        assert deleted
        assert await repo.get(created["id"]) is None

    async def test_delete_nonexistent(self, db_session):
        repo = SessionRepository(db_session)
        result = await repo.delete("nonexistent")
        assert not result

    async def test_interrupt_running(self, db_session):
        repo = SessionRepository(db_session)
        s1 = await repo.create("demo", "Running")
        s2 = await repo.create("demo", "Completed")
        s3 = await repo.create("demo", "Idle")

        await repo.update_status(s1["id"], "running")
        await repo.update_status(s2["id"], "completed")

        count = await repo.interrupt_running()
        assert count == 1

        assert (await repo.get(s1["id"]))["status"] == "interrupted"
        assert (await repo.get(s2["id"]))["status"] == "completed"
        assert (await repo.get(s3["id"]))["status"] == "idle"
