"""对象存储连接 ORM model。"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class ObjectStorageConnection(TimestampMixin, Base):
    """用户自有对象存储的连接配置，独立于媒体生成供应商的凭证。

    首期只有 TOS 一种 ``kind``，实例至多保存一条连接。``access_key_id`` / ``secret_access_key``
    明文落库，读出 API 时经 ``mask_secret`` 掩码，与供应商凭证同一规则。删除的连接 ID 不回收：
    之后保存在对象存储里的内容按连接 ID 记录所属连接，复用 ID 会让旧定位指向另一个桶。
    """

    __tablename__ = "object_storage_connection"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    access_key_id: Mapped[str] = mapped_column(Text, nullable=False)
    secret_access_key: Mapped[str] = mapped_column(Text, nullable=False)
