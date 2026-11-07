"""Instance-local state of provider models belonging to built-in providers."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, Index, String, UniqueConstraint
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class BuiltinProviderModelState(TimestampMixin, Base):
    """Local state for one provider model under a built-in provider (docs/adr/0099).

    Each row represents one of two cases:

    - Enabled state for an officially listed model. The model list and every field follow
      official data; record only user enable/disable choices here. Without a row, models
      are enabled unless their entries are offline.
    - User-added model (``user_added``): the user owns API model name, media type, endpoint,
      provider-model multiplier, entry binding and capability overrides. If official data
      later adds the same model key, only this row's enabled state remains effective.

    ``model_id`` is the model key used by settings and projects: catalog-query keys for
    officially listed models, or API model names for user-added models.
    """

    __tablename__ = "builtin_provider_model"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_id", name="uq_builtin_provider_model"),
        Index("ix_builtin_provider_model_provider_id", "provider_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    user_added: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The following columns apply only to user-added models; officially listed models ignore them.
    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default=sa_text("1"))
    # Entry binding (docs/adr/0105): model vendor/official model name, with media type from ``media_type``. NULL means live name lookup.
    entry_binding: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Capability overrides (docs/adr/0104): sparse dictionary with the same keys as custom-provider-model overrides.
    capability_overrides: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    # Attached built-in endpoint, one of the provider's available endpoints for ``media_type``. NULL means
    # unrecorded; assembly selects it by official-model rules (lib.backends.backend_assembly.specs.builtin_model_endpoint).
    endpoint: Mapped[str | None] = mapped_column(String(64), nullable=True)
