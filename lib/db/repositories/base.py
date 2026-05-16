"""Repository base class with query scoping support."""

from typing import Any

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.base import Base


class BaseRepository:
    """Repository base class. Provides _scope_query override point."""

    def __init__(self, session: AsyncSession):
        self.session = session

    def _scope_query(self, stmt: Select, model: type[Base]) -> Select:
        """Query scope limiter. Subclasses can override to inject additional filters."""
        return stmt


def rowcount(result: Any) -> int:
    """SQLAlchemy AsyncResult.rowcount 在当前 stub 中是 Any，统一在此 narrow。"""
    return result.rowcount or 0
