"""User-modified model-entry copies."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Integer, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class CatalogUserEntry(TimestampMixin, Base):
    """A user-modified model entry: a complete copy, one row per entry (docs/adr/0099).

    A row stops following official data and uses only this copy; restoring official data
    deletes it. Without an official entry of the same identity, the row is user-created.
    ``entry`` is catalog-format entry JSON (``arcreel_catalog_core.dump_entry``), including
    pricing; reads run instance-side validation. ``user_created`` marks entries created
    because a model was missing from the catalog. If official data later adds that identity,
    prompt to switch to official data rather than restore official data, as for user-edited official entries.
    """

    __tablename__ = "catalog_user_entry"
    __table_args__ = (UniqueConstraint("maker", "name", "media_type", name="uq_catalog_user_entry"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    maker: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entry: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    user_created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
