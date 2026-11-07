"""Custom invocation-endpoint ORM model."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class CustomEndpoint(TimestampMixin, Base):
    """Invocation-endpoint definition imported or manually created by the user.

    The complete ``definition`` JSON is the sole source of truth. Other columns are
    read-only mirrors derived at write time for DB filtering/sorting without JSON parsing
    (``docs/adr/0067``). Never edit mirrors independently: rename through ``meta.name``
    and change protocol through the definition itself.

    Global entity without a user dimension, like :class:`~lib.db.models.custom_provider.CustomProvider`.
    ``display_name`` is not unique: duplicate imports prompt rather than reject; clients
    handle lineage determined by ``meta.author + meta.name``.
    """

    __tablename__ = "custom_endpoint"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Container kind from definition.kind (declarative / comfyui; Python plugins are a reserved slot for a separate topic)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Definition format version; preserve the file value unchanged, without rewriting or migration
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # From definition media_type; defaults to "video" when omitted in declarative definitions
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)  # ← definition.meta.name
