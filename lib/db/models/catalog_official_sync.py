"""Catalog data retrieved by official sync and its synchronization status."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class CatalogOfficialSync(TimestampMixin, Base):
    """Official synchronization state, containing only one row (docs/adr/0103).

    ``catalog`` is the last applied data file (catalog-format JSON), supplemented with
    entries deleted upstream whose last versions are retained locally. Fetching already
    verified manifest hashes; reading performs only instance-side parsing. Before any
    successful fetch, ``dataset_version`` and ``catalog`` are empty. ``last_success_at``
    records the last successful check (including unchanged dataset versions).
    ``last_error`` records the most recent check failure and is cleared on a successful check.
    """

    __tablename__ = "catalog_official_sync"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    dataset_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    catalog: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
