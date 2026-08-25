"""User and account-center login models."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=sa.true())
    account_center_sub: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    account_center_roles: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    account_center_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arcreel_cloud_sub: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)


class AccountCenterLoginTicket(Base):
    """Short-lived, single-use handoff from OIDC callback to the SPA."""

    __tablename__ = "account_center_login_tickets"

    ticket_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    account_center_sub: Mapped[str] = mapped_column(String, nullable=False)
    username: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    local_user_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    device_token_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)


class AccountCenterConnection(TimestampMixin, Base):
    """Persistent local connector bound to one ArcReel user and center identity."""

    __tablename__ = "account_center_connections"

    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    account_center_sub: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    device_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    device_token_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    config_revision: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, server_default="0")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ArcReelCloudSession(TimestampMixin, Base):
    """Rotating cloud session for one local ArcReel shadow user."""

    __tablename__ = "arcreel_cloud_sessions"

    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    cloud_user_sub: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    refresh_token_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    config_revision: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, server_default="0")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
