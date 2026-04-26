"""Custom provider ORM models."""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class CustomProvider(TimestampMixin, Base):
    """用户自定义的 AI 供应商。"""

    __tablename__ = "custom_provider"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    discovery_format: Mapped[str] = mapped_column(String(32), nullable=False)  # "openai" | "google"
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)  # sensitive, masked in API responses

    @property
    def provider_id(self) -> str:
        from lib.custom_provider import make_provider_id

        return make_provider_id(self.id)


class CustomProviderModel(TimestampMixin, Base):
    """自定义供应商下的模型配置。"""

    __tablename__ = "custom_provider_model"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_id", name="uq_custom_provider_model"),
        Index("ix_custom_provider_model_provider_id", "provider_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("custom_provider.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(32), nullable=False)  # ENDPOINT_REGISTRY key
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    price_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "token" | "image" | "second"
    price_input: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_output: Mapped[float | None] = mapped_column(Float, nullable=True)  # only for text
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)  # "USD" | "CNY"
    supported_durations: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list[int]
    resolution: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # standard token ("1080p"/"2K") or native "WxH"
