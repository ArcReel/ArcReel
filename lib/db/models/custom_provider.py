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
    """User-defined AI provider."""

    __tablename__ = "custom_provider"
    # Keep synchronized with the column-adding migration: all three concurrency-limit columns enforce ≥1
    # at the DB level, rejecting zero/negative values even through direct repository writes or SQL.
    # NULL means unset, falling back to defaults. Zero is not valid user input; it is only an internal
    # unsupported-lane sentinel in CapacityTable, projected in memory and never persisted to these columns.
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
        # Never reuse deleted provider IDs: provider_id (custom-<id>) appears in failure and usage records; reuse would misattribute them
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    discovery_format: Mapped[str] = mapped_column(String(32), nullable=False)  # "openai" | "google"
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)  # sensitive, masked in API responses
    # Typed concurrency-limit columns named by lane; NULL means unset, so capacity loading uses global defaults.
    # Custom providers are absent from the built-in registry and have no declaration-default layer:
    # fallback has two levels (user column value → global default).
    image_max_workers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_max_workers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_max_workers: Mapped[int | None] = mapped_column(Integer, nullable=True)

    @property
    def provider_id(self) -> str:
        from lib.custom_provider import make_provider_id

        return make_provider_id(self.id)


class CustomProviderModel(TimestampMixin, Base):
    """Model configuration under a custom provider."""

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
    resolution: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # standard token ("1080p"/"2K") or native "WxH"
    # Model-level capability overrides, a sparse dictionary keyed by VideoCapabilities field names.
    # NULL or an absent key follows system determination. No fixed capability-dimension list enters
    # the schema, so new dimensions need no migration. lib.custom_provider.capabilities alone owns composition semantics.
    capability_overrides: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    # Path overrides: endpoint request name → complete replacement path (docs/adr/0101). NULL uses endpoint defaults.
    # Writers clear these on endpoint changes; reads and execution ignore request names absent from the definition.
    path_overrides: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    # Provider-model multiplier (docs/adr/0100): multiply by the provider multiplier and apply to the whole model reference cost; default 1.
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default=sa_text("1"))
    # Entry binding (docs/adr/0105): user-selected model entry stored as model vendor/official model name;
    # media type comes from the endpoint. NULL or a bound entry missing from the catalog falls back to live name lookup.
    entry_binding: Mapped[str | None] = mapped_column(String(255), nullable=True)
