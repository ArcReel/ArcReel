"""Custom provider ORM models."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class CustomProvider(TimestampMixin, Base):
    """用户自定义的 AI 供应商。"""

    __tablename__ = "custom_provider"
    __table_args__ = (
        CheckConstraint(
            "image_max_workers IS NULL OR image_max_workers >= 1",
            name="ck_custom_provider_image_max_workers_positive",
        ),
        CheckConstraint(
            "video_max_workers IS NULL OR video_max_workers >= 1",
            name="ck_custom_provider_video_max_workers_positive",
        ),
        CheckConstraint(
            "audio_max_workers IS NULL OR audio_max_workers >= 1",
            name="ck_custom_provider_audio_max_workers_positive",
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    discovery_format: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    image_max_workers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_max_workers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_max_workers: Mapped[int | None] = mapped_column(Integer, nullable=True)

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
    endpoint: Mapped[str] = mapped_column(String(32), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    resolution: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capability_overrides: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    path_overrides: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default=sa_text("1"))
    entry_binding: Mapped[str | None] = mapped_column(String(255), nullable=True)
