"""Provider config and system setting ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, utc_now


class ProviderConfig(Base):
    __tablename__ = "provider_config"
    __table_args__ = (
        UniqueConstraint("provider", "key", name="uq_provider_key"),
        Index("ix_provider_config_provider", "provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ManagedProviderConfig(Base):
    """Per-user provider overrides delivered by a trusted management plane.

    The original ``provider_config`` table remains the installation-wide fallback.
    Keeping managed values in a separate table preserves existing local behaviour
    while preventing two cloud accounts on the same ArcReel installation from
    overwriting each other's advanced provider settings.
    """

    __tablename__ = "managed_provider_config"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            "key",
            "management_source",
            name="uq_managed_provider_config_identity",
        ),
        Index("ix_managed_provider_config_user_provider", "user_id", "provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    management_source: Mapped[str] = mapped_column(String(32), nullable=False)
    management_revision: Mapped[int] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class SystemSetting(Base):
    __tablename__ = "system_setting"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
