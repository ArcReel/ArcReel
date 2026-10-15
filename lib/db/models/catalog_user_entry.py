"""用户修改的模型条目副本。"""

from __future__ import annotations

from sqlalchemy import JSON, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class CatalogUserEntry(TimestampMixin, Base):
    """用户修改的一个模型条目：整个条目的副本，每个条目一行（docs/adr/0099）。

    有行即这个条目停止跟随官方，只用这份副本；恢复官方数据即删除这一行。官方数据里没有同一身份的条目时，
    这一行是用户新建的条目。``entry`` 是目录数据格式的条目 JSON（``arcreel_catalog_core.dump_entry``），
    含价目表；读入时过实例侧校验。
    """

    __tablename__ = "catalog_user_entry"
    __table_args__ = (UniqueConstraint("maker", "name", "media_type", name="uq_catalog_user_entry"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    maker: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entry: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
