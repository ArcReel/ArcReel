"""Tests for ORM model definitions — verify tables can be created."""

from datetime import UTC, datetime

from sqlalchemy import inspect, select

import lib.db.models  # noqa: F401 — ensure all models registered for Base.metadata
from lib.db.base import TimestampMixin, UserOwnedMixin
from lib.db.models import AgentSession, Task, User


class TestModelsCreateTables:
    async def test_all_tables_exist(self, db_engine):
        async with db_engine.connect() as conn:
            table_names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "tasks" in table_names
        assert "worker_lease" in table_names
        assert "api_calls" in table_names
        assert "agent_sessions" in table_names

    async def test_task_round_trip(self, db_session):
        now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
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
        db_session.add(task)
        await db_session.commit()

        from sqlalchemy import select

        result = await db_session.execute(select(Task).where(Task.task_id == "abc123"))
        loaded = result.scalar_one()
        assert loaded.project_name == "demo"
        assert loaded.status == "queued"

    async def test_agent_session_round_trip(self, db_session):
        now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        s = AgentSession(
            id="sess123",
            sdk_session_id="sdk-sess123",
            project_name="demo",
            status="idle",
            created_at=now,
            updated_at=now,
        )
        db_session.add(s)
        await db_session.commit()

        from sqlalchemy import select

        result = await db_session.execute(select(AgentSession).where(AgentSession.id == "sess123"))
        loaded = result.scalar_one()
        assert loaded.project_name == "demo"


class TestUserModel:
    async def test_user_model_columns(self, db_engine):
        """Verify User table has correct columns."""
        async with db_engine.connect() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("users")}
            )
        assert columns == {"id", "username", "role", "is_active", "created_at", "updated_at"}

    async def test_user_round_trip(self, db_session):
        user = User(id="u1", username="alice")
        db_session.add(user)
        await db_session.commit()

        result = await db_session.execute(select(User).where(User.id == "u1"))
        loaded = result.scalar_one()
        assert loaded.username == "alice"
        assert loaded.role == "user"  # server_default
        assert loaded.created_at is not None
        assert loaded.updated_at is not None


class TestTimestampMixin:
    def test_timestamp_mixin_defaults(self):
        """Verify TimestampMixin provides default and onupdate."""
        assert hasattr(TimestampMixin, "created_at")
        assert hasattr(TimestampMixin, "updated_at")
        # Access the underlying Column via MappedColumn.column
        col_created = TimestampMixin.__dict__["created_at"].column
        col_updated = TimestampMixin.__dict__["updated_at"].column
        assert col_created.default is not None
        assert col_updated.default is not None
        assert col_updated.onupdate is not None


class TestUserOwnedMixin:
    def test_user_owned_mixin_server_default(self):
        """Verify UserOwnedMixin has server_default='default'."""
        col = UserOwnedMixin.__dict__["user_id"].column
        assert col.server_default is not None
        assert col.server_default.arg == "default"


class TestMixinApplicationToModels:
    """Verify Mixin columns are present on ORM models after refactoring."""

    async def test_task_has_user_id(self, db_engine):
        """Task model should have user_id from UserOwnedMixin."""
        async with db_engine.connect() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("tasks")}
            )
        assert "user_id" in columns

    async def test_api_call_has_timestamp_and_user_id(self, db_engine):
        """ApiCall should have created_at (NOT NULL), updated_at, and user_id from Mixins."""
        async with db_engine.connect() as conn:
            col_info = await conn.run_sync(
                lambda sync_conn: {c["name"]: c for c in inspect(sync_conn).get_columns("api_calls")}
            )
        assert "created_at" in col_info
        assert col_info["created_at"]["nullable"] is False
        assert "updated_at" in col_info
        assert "user_id" in col_info

    async def test_api_key_has_timestamp_and_user_id(self, db_engine):
        """ApiKey should have updated_at and user_id from Mixins."""
        async with db_engine.connect() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("api_keys")}
            )
        assert "updated_at" in columns
        assert "user_id" in columns

    async def test_agent_session_has_timestamp_and_user_id(self, db_engine):
        """AgentSession should have created_at, updated_at, and user_id from Mixins."""
        async with db_engine.connect() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("agent_sessions")}
            )
        assert "created_at" in columns
        assert "updated_at" in columns
        assert "user_id" in columns

    async def test_worker_lease_no_user_id(self, db_engine):
        """WorkerLease should NOT have user_id — it was not given UserOwnedMixin."""
        async with db_engine.connect() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("worker_lease")}
            )
        assert "user_id" not in columns
