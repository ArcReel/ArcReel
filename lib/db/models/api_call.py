"""API call usage tracking ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, Index, Integer, String, Text
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
    # Meaningful only for video calls; NULL when unspecified, without defaults for nonvideo rows.
    generate_audio: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    # Machine-stable failure form (error code + parameters) for frontend locale rendering; error_message retains original text.
    error_code: Mapped[str | None] = mapped_column(String)
    error_params: Mapped[object | None] = mapped_column(JSON, nullable=True)
    # Artifact path: project-relative, matching paths in inputs.
    output_path: Mapped[str | None] = mapped_column(Text)
    segment_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # Generation task served by this call; leave empty for taskless calls (four text sites, assistant sessions, endpoint trials), with origin in purpose.
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    purpose: Mapped[str | None] = mapped_column(String, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Inputs sent by this call: reference images [{path, label, role}], first / last frames, voice, and
    # request parameters without dedicated columns. Paths are project-relative (normalization in lib/generation/media_generator.py).
    inputs: Mapped[object | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    retry_count: Mapped[int] = mapped_column(Integer, server_default="0")
    # NULL means unpriced, distinct from zero-priced 0; aggregates count only non-NULL rows greater than 0.
    # The server default 0.0 is for newly started pending rows. ORM inserts replace Python None with the
    # default; use sqlalchemy.null() to write NULL. UPDATE does not have this limitation.
    cost_amount: Mapped[float | None] = mapped_column(Float, nullable=True, server_default="0.0")
    currency: Mapped[str] = mapped_column(String, server_default="USD")
    provider: Mapped[str] = mapped_column(String, server_default="gemini")
    usage_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_provider_response: Mapped[object | None] = mapped_column(JSON, nullable=True)
    # Reason for unpriced cost_amount NULL; empty for priced rows.
    unpriced_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # input_tokens remains total input including cache; uncached input = input_tokens - cache reads - both cache-write tiers.
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_5m_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_1h_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (
        Index("idx_api_calls_project_name", "project_name"),
        Index("idx_api_calls_call_type", "call_type"),
        Index("idx_api_calls_status", "status"),
        Index("idx_api_calls_started_at", "started_at"),
        Index("idx_api_calls_task_id", "task_id"),
    )
