"""User-message identity-mapping ORM model: event-log entry ↔ SDK transcript entry."""

from __future__ import annotations

from sqlalchemy import PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin, UserOwnedMixin


class AgentSessionUserMessageLink(TimestampMixin, UserOwnedMixin, Base):
    """Mapping from ArcReel-synthesized user-message IDs to SDK transcript entry UUIDs.

    The event log (agent_session_event_log) and transcript mirror (agent_session_entries)
    have independent identity systems; a user message has an identity in each. This table
    persists their correspondence so session forks can locate transcript truncation points
    from event-log entries.

    Write the mapping when the SDK replays user-message copies. Missing mappings mean
    the position cannot be located; session read paths still work normally without mapping rows.
    """

    __tablename__ = "agent_session_user_message_links"

    session_id: Mapped[str] = mapped_column(String, nullable=False)
    # UUID in the event-log user-entry payload (user-<hex>).
    user_entry_uuid: Mapped[str] = mapped_column(String, nullable=False)
    # agent_session_entries.uuid: this user message entry identity in the SDK transcript.
    sdk_entry_uuid: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (PrimaryKeyConstraint("session_id", "user_entry_uuid"),)
