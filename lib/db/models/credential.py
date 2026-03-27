"""Provider credential ORM model."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class ProviderCredential(TimestampMixin, Base):
    """供应商凭证。每个供应商可有多条凭证，其中最多一条 is_active=True。"""

    __tablename__ = "provider_credential"
    __table_args__ = (
        Index("ix_provider_credential_provider", "provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    credentials_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def overlay_config(self, config: dict[str, str]) -> dict[str, str]:
        """将凭证字段合并到配置字典中，返回修改后的 config。"""
        if self.api_key:
            config["api_key"] = self.api_key
        if self.credentials_path:
            config["credentials_path"] = self.credentials_path
        if self.base_url:
            config["base_url"] = self.base_url
        return config
