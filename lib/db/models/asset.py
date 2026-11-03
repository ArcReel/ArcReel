"""Asset ORM: 全局资产库条目。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class GlobalImageColumns:
    """库里一张图片的内容事实，见 ``lib.material.global_library``。

    ``image_digest`` 是库存储文件的裸 sha256；``image_content_id`` 是这张图片在全局资产库里的内容身份，
    供应商素材关联与正式对象引用以它为键（持有方为条目键 ``library_holder(asset_id)``），换图时重新生成；
    ``image_storage`` 是远端持久定位（``ObjectLocator.to_record()`` 的形状），有它时 ``image_path`` 只是工作文件。
    三列都为空的存量行按本地文件解释。
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
    """全局资产库里挂在一条角色资产下的衍生（见 ``docs/adr/0072``）。

    衍生随本体资产整套进出资产库：``from-project`` 把角色条目里的衍生表连同资产图一起
    写进本表，``apply-to-project`` 再把它们写回目标项目的角色条目并落盘。名字只在所属
    资产内唯一，与项目内的衍生表同口径。
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
