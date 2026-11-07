"""Asset ORM: global asset-library entries."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class GlobalImageColumns:
    """Content facts for one library image; see ``lib.material.global_library``.

    ``image_digest`` is the stored file's bare sha256. ``image_content_id`` identifies this
    image's content in the global library, keying provider asset links and formal object
    references (held by entry key ``library_holder(asset_id)``); regenerate it on image replacement.
    ``image_storage`` is its persistent remote locator (the ``ObjectLocator.to_record()`` shape);
    when present, ``image_path`` is only a working file. Interpret legacy rows with all three
    columns empty as local files.
    """

    image_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_content_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_storage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Asset(GlobalImageColumns, TimestampMixin, Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("type", "name", name="uq_asset_type_name"),
        Index("ix_asset_type", "type"),
        Index("ix_asset_name", "name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # character/scene/prop
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    voice_style: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_project: Mapped[str | None] = mapped_column(String(200), nullable=True)


class AssetDerivative(GlobalImageColumns, TimestampMixin, Base):
    """A derivative belonging to a character asset in the global library (``docs/adr/0072``).

    Derivatives enter and leave the library together with the base asset: ``from-project``
    writes the character's derivative table and asset images here; ``apply-to-project``
    writes and persists them back to the target project's character entry. Names are unique
    only within the owning asset, matching the project derivative table.
    """

    __tablename__ = "asset_derivatives"
    __table_args__ = (
        UniqueConstraint("asset_id", "name", name="uq_asset_derivative_asset_name"),
        Index("ix_asset_derivative_asset_id", "asset_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
