"""Tests for ORM model definitions — verify tables can be created."""

import pytest
from datetime import datetime, timezone
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from lib.db.base import Base
from lib.db.models import Task, AgentSession


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestModelsCreateTables:
    async def test_all_tables_exist(self, engine):
        async with engine.connect() as conn:
            table_names = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        assert "tasks" in table_names
        assert "task_events" in table_names
        assert "worker_lease" in table_names
        assert "api_calls" in table_names
        assert "agent_sessions" in table_names

    async def test_task_round_trip(self, session):
        now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        task = Task(
            task_id="abc123",
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            status="queued",
            queued_at=now,
            updated_at=now,
        )
        session.add(task)
        await session.commit()

        from sqlalchemy import select
        result = await session.execute(select(Task).where(Task.task_id == "abc123"))
        loaded = result.scalar_one()
        assert loaded.project_name == "demo"
        assert loaded.status == "queued"

    async def test_agent_session_round_trip(self, session):
        now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        s = AgentSession(
            id="sess123",
            project_name="demo",
            status="idle",
            created_at=now,
            updated_at=now,
        )
        session.add(s)
        await session.commit()

        from sqlalchemy import select
        result = await session.execute(select(AgentSession).where(AgentSession.id == "sess123"))
        loaded = result.scalar_one()
        assert loaded.project_name == "demo"
