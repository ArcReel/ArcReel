"""Tests for BaseRepository and _scope_query mechanism."""

from sqlalchemy import select

from lib.db.models import Task
from lib.db.repositories.base import BaseRepository


class TestBaseRepository:
    def test_scope_query_noop(self):
        """_scope_query returns stmt unchanged by default."""
        repo = BaseRepository.__new__(BaseRepository)
        stmt = select(Task)
        result = repo._scope_query(stmt, Task)
        assert str(result) == str(stmt)

    def test_scope_query_overridable(self):
        """Subclass can override _scope_query to add filters."""

        class ScopedRepo(BaseRepository):
            def _scope_query(self, stmt, model):
                return stmt.where(model.user_id == "test-user")

        repo = ScopedRepo.__new__(ScopedRepo)
        stmt = select(Task)
        result = repo._scope_query(stmt, Task)
        assert "user_id" in str(result)
