"""API call usage tracking ORM model."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin, UserOwnedMixin


class ApiCall(TimestampMixin, UserOwnedMixin, Base):
    __tablename__ = "api_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    call_type: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(String)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    aspect_ratio: Mapped[str | None] = mapped_column(String)
    generate_audio: Mapped[bool | None] = mapped_column(Boolean, server_default=sa.true())
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    output_path: Mapped[str | None] = mapped_column(Text)
    segment_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_amount: Mapped[float] = mapped_column(Float, server_default="0.0")
    currency: Mapped[str] = mapped_column(String, server_default="USD")
    provider: Mapped[str] = mapped_column(String, server_default="gemini")
    usage_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (
        Index("idx_api_calls_project_name", "project_name"),
        Index("idx_api_calls_call_type", "call_type"),
        Index("idx_api_calls_status", "status"),
        Index("idx_api_calls_started_at", "started_at"),
    )
