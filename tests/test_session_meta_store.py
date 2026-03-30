"""Tests for SessionMetaStore (async wrapper over SessionRepository)."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from server.agent_runtime.session_store import SessionMetaStore


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    s = SessionMetaStore(session_factory=factory)
    yield s
    await engine.dispose()


class TestSessionMetaStore:
    async def test_session_lifecycle(self, store):
        session = await store.create(project_name="demo", sdk_session_id="sdk-abc")
        assert session.project_name == "demo"
        assert session.status == "idle"
        assert session.id == "sdk-abc"

        sessions = await store.list(project_name="demo")
        assert len(sessions) == 1
        assert sessions[0].id == session.id

        # Test status update
        updated = await store.update_status(session.id, "running")
        assert updated

        running_session = await store.get(session.id)
        assert running_session is not None
        assert running_session.status == "running"

        # Test delete
        deleted = await store.delete(session.id)
        assert deleted
        assert await store.get(session.id) is None

    async def test_list_with_filters(self, store):
        # Create sessions for different projects
        await store.create(project_name="project_a", sdk_session_id="sdk-a1")
        await store.create(project_name="project_a", sdk_session_id="sdk-a2")
        await store.create(project_name="project_b", sdk_session_id="sdk-b1")

        # Filter by project
        sessions_a = await store.list(project_name="project_a")
        assert len(sessions_a) == 2

        sessions_b = await store.list(project_name="project_b")
        assert len(sessions_b) == 1

        # Filter by status
        await store.update_status(sessions_a[0].id, "completed")
        completed = await store.list(status="completed")
        assert len(completed) == 1

    async def test_delete_nonexistent(self, store):
        deleted = await store.delete("nonexistent-id")
        assert not deleted

    async def test_interrupt_running_sessions(self, store):
        running = await store.create(project_name="demo", sdk_session_id="sdk-running")
        completed = await store.create(project_name="demo", sdk_session_id="sdk-completed")
        idle = await store.create(project_name="demo", sdk_session_id="sdk-idle")

        await store.update_status(running.id, "running")
        await store.update_status(completed.id, "completed")

        interrupted_count = await store.interrupt_running_sessions()

        assert interrupted_count == 1
        assert (await store.get(running.id)).status == "interrupted"
        assert (await store.get(completed.id)).status == "completed"
        assert (await store.get(idle.id)).status == "idle"

    async def test_id_equals_sdk_session_id(self, store):
        """SessionMeta.id 应直接映射 sdk_session_id 值。"""
        session = await store.create(project_name="demo", sdk_session_id="my-sdk-session-42")
        assert session.id == "my-sdk-session-42"
