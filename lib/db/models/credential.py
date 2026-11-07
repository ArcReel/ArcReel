"""Provider credential ORM model."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin
from lib.infra.data_root_layout import DataRootLayout


class ProviderCredential(TimestampMixin, Base):
    """Provider credentials. Each provider may have multiple credentials, with at most one is_active=True."""

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
    # Typed column named by registry key for built-in providers requiring two secret strings
    # (Kling access_key + secret_key, JWT HS256 authentication). Sparse and always NULL except for Kling (ADR 0037).
    access_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def credentials_file(self) -> str | None:
        """Credential file location: derive Vertex paths from the ID through the data-root layout; fall back to the recorded path if that file is absent."""
        if self.provider == "gemini-vertex":
            derived = DataRootLayout.current().vertex_credential_path(self.id)
            if derived.is_file():
                return str(derived)
        return self.credentials_path

    def overlay_config(self, config: dict[str, str]) -> dict[str, str]:
        """Merge credential fields into the configuration dictionary and return the modified config.

        Column names are config dictionary keys (registry key = form field = backend
        constructor argument = config key throughout, without a translation layer; ADR 0037).
        """
        if self.api_key:
            config["api_key"] = self.api_key
        if credentials_file := self.credentials_file():
            config["credentials_path"] = credentials_file
        if self.base_url:
            config["base_url"] = self.base_url
        if self.access_key:
            config["access_key"] = self.access_key
        if self.secret_key:
            config["secret_key"] = self.secret_key
        return config
