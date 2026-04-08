"""Provider credential ORM model."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class ProviderCredential(TimestampMixin, Base):
    """Provider credential. Each provider may have multiple credentials, with at most one is_active=True."""

    __tablename__ = "provider_credential"
    __table_args__ = (
        Index("ix_provider_credential_provider", "provider"),
        Index(
            "uq_provider_credential_one_active",
            "provider",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    credentials_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def overlay_config(self, config: dict[str, str]) -> dict[str, str]:
        """Merge credential fields into the config dict and return the modified config."""
        if self.api_key:
            config["api_key"] = self.api_key
        if self.credentials_path:
            config["credentials_path"] = self.credentials_path
        if self.base_url:
            config["base_url"] = self.base_url
        return config
