"""SQLAlchemy declarative base."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DEFAULT_USER_ID = "default"


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def dt_to_iso(val: datetime | None) -> str | None:
    """Serialize database timestamps as unambiguous UTC ISO strings.

    SQLite discards timezone metadata when reading ``DateTime(timezone=True)``
    columns back. Application timestamps are written in UTC, so a naive value
    from SQLite must be restored to UTC before it crosses an API boundary.
    """

    if val is None:
        return None
    if val.tzinfo is None:
        val = val.replace(tzinfo=UTC)
    return val.astimezone(UTC).isoformat()


class TimestampMixin:
    """Unified created/updated timestamps."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class UserOwnedMixin:
    """User ownership marker."""

    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        server_default=DEFAULT_USER_ID,
        index=True,
    )
