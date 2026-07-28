"""Asset ORM: 全局资产库条目。"""

from __future__ import annotations

from sqlalchemy import Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class Asset(TimestampMixin, Base):
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
    source_project: Mapped[str | None] = mapped_column(String(200), nullable=True)
