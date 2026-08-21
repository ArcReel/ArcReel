"""Asset ORM: 全局资产库条目及其多媒体资源。"""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lib.db.base import Base, TimestampMixin


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("type", "name", name="uq_asset_type_name"),
        UniqueConstraint("external_source", "external_id", name="uq_asset_external_identity"),
        Index("ix_asset_type", "type"),
        Index("ix_asset_name", "name"),
        Index("ix_asset_external_identity", "external_source", "external_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # character/scene/prop
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    voice_style: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_project: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    voice_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resources: Mapped[list[AssetResource]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="AssetResource.sort_order",
    )
    aliases: Mapped[list[AssetAlias]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="AssetAlias.sort_order",
    )


class AssetAlias(TimestampMixin, Base):
    """A structured alternate name used to identify one global asset."""

    __tablename__ = "asset_aliases"
    __table_args__ = (
        UniqueConstraint("asset_id", "comparison_key", name="uq_asset_alias_comparison_key"),
        CheckConstraint("origin IN ('catalog', 'local')", name="ck_asset_alias_origin"),
        Index("ix_asset_alias_comparison_key", "comparison_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    comparison_key: Mapped[str] = mapped_column(String(200), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    asset: Mapped[Asset] = relationship(back_populates="aliases")


class AssetResource(TimestampMixin, Base):
    """一条全局资产关联的可下载媒体。

    ``assets.image_path`` / ``audio_path`` 继续作为现有生成链路的主资源投影；
    本表保留同一角色的全部图片和参考音频；目录中的视频不进入人物资产库。
    """

    __tablename__ = "asset_resources"
    __table_args__ = (
        UniqueConstraint("asset_id", "resource_key", name="uq_asset_resource_key"),
        CheckConstraint("media_type IN ('image', 'audio')", name="ck_asset_resource_media_type"),
        Index("ix_asset_resource_asset_media", "asset_id", "media_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_key: Mapped[str] = mapped_column(String(300), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="catalog")
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revision: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_fields_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    asset: Mapped[Asset] = relationship(back_populates="resources")
