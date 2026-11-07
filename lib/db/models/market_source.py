"""Market-source ORM model: global entity, without a user dimension."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class MarketSource(TimestampMixin, Base):
    """A market source registered with this instance.

    ``cached_index`` is the raw index from the last successful fetch; ``fetched_at`` is
    the last successful refresh time (including 304). Failed refreshes change only
    ``status`` / ``last_error``, preserving both cached fields.

    ``id`` is monotonic and never reused even on SQLite (``AUTOINCREMENT``). Frontend
    filtering, in-flight refreshes and reordering identify sources by ID; a source added
    after deletion must not inherit the deleted source's ID.
    """

    __tablename__ = "market_source"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    address: Mapped[str] = mapped_column(String(2048), nullable=False)
    index_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_index: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
