"""Session event-log ORM model: the UI timeline sole read source."""

from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Index, PrimaryKeyConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin, UserOwnedMixin


class AgentSessionEventLogEntry(TimestampMixin, UserOwnedMixin, Base):
    """Session event-log entry: append-only, with monotonically increasing seq per session.

    Coexistence with the SDK transcript mirror (agent_session_entries) is intentional
    dual writing: this table is the UI read model, shaped at write time; transcript is Agent memory.
    """

    __tablename__ = "agent_session_event_log"

    session_id: Mapped[str] = mapped_column(String, nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Request-side idempotency key for accepting user messages; retries return the existing entry without duplication.
    client_key: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("session_id", "seq"),
        Index(
            "uq_agent_event_log_client_key",
            "session_id",
            "client_key",
            unique=True,
            postgresql_where=text("client_key IS NOT NULL"),
            sqlite_where=text("client_key IS NOT NULL"),
        ),
        # Lookup index for cross-session idempotency fallback (find_new_session_by_client_key):
        # the unique index starts with session_id and cannot serve client_key-only lookups.
        Index(
            "ix_agent_event_log_client_key",
            "client_key",
            postgresql_where=text("client_key IS NOT NULL"),
            sqlite_where=text("client_key IS NOT NULL"),
        ),
    )
