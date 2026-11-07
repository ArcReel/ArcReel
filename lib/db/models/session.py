"""Agent session ORM model."""

from __future__ import annotations

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin, UserOwnedMixin


class AgentSession(TimestampMixin, UserOwnedMixin, Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    sdk_session_id: Mapped[str] = mapped_column(String, unique=True)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, server_default="")
    status: Mapped[str] = mapped_column(String, server_default="idle")
    # Branch-session pointer: nonempty means this session was superseded by a rewrite fork (docs/adr/0058).
    # The pointer itself is the marker, leaving status for lifecycle; idle eviction and startup
    # interrupt both operate on status.
    superseded_by: Mapped[str | None] = mapped_column(String, nullable=True)
    # Fork lineage: originating session and message, immutable after row creation.
    # fork_anchor_uuid belongs to the transcript domain. The anchor itself stays only in the original
    # session, excluded from prefix copying; historical messages retain identical transcript UUIDs
    # across branches, providing alignment without additional mapping.
    fork_parent_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    fork_anchor_uuid: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("idx_agent_sessions_project", "project_name", "updated_at"),
        Index("idx_agent_sessions_status", "status"),
    )
