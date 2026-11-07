"""Sharing submission records: local endpoint bindings to official-service submission tokens and the last retrieved status."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, utc_now


class MarketSubmission(Base):
    """Keep only the latest submission per endpoint; resubmission overwrites it with the official-service token."""

    __tablename__ = "market_submission"

    custom_endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("custom_endpoint.id", ondelete="CASCADE"), primary_key=True
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    #: ``open`` / ``merged`` / ``closed``; retain the last retrieved value when the official service is unreachable.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    pr_url: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
