"""Agent session ORM model."""

from __future__ import annotations

from datetime import datetime
from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    sdk_session_id: Mapped[str] = mapped_column(String, unique=True)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, server_default="")
    status: Mapped[str] = mapped_column(String, server_default="idle")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_agent_sessions_project", "project_name", "updated_at"),
        Index("idx_agent_sessions_status", "status"),
    )
