"""供应商素材库控制面账号与推理凭证绑定的 ORM model。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class AssetLibraryAccount(TimestampMixin, Base):
    """一个供应商素材库的资源范围：站点、区域、项目，连同访问该范围的控制面凭证。

    控制面凭证（Access Key ID / Secret Access Key）与推理 API Key 互不相通，明文落库、读出 API
    时掩码。控制面请求只发往 ``site`` 与 ``region`` 在代码中对应的官方主机，不从推理凭证的
    base_url 推断。删除的账号 ID 不回收，避免之后的供应商素材关联误指向另一个范围。
    """

    __tablename__ = "asset_library_account"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    site: Mapped[str] = mapped_column(String(16), nullable=False)  # "volcengine" | "byteplus"
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    project_name: Mapped[str] = mapped_column(String(128), nullable=False)
    access_key_id: Mapped[str] = mapped_column(Text, nullable=False)
    secret_access_key: Mapped[str] = mapped_column(Text, nullable=False)


class AssetLibraryBinding(TimestampMixin, Base):
    """把一个素材库资源范围显式绑定到一条可以使用它的推理凭证。

    ``credential_type`` 为 ``builtin`` 时 ``credential_id`` 指 ``provider_credential.id``，为
    ``custom`` 时指 ``custom_provider.id``；两种目标不在同一张表，故不建外键。每条推理凭证至多
    绑定一个范围。``confirmed_fingerprint`` 是确认时推理凭证内容、endpoint 与素材库范围的摘要：
    读取时与当前值重新计算比对，不一致即视为待重新确认，不因凭证记录 ID 未变而继承旧资格。
    """

    __tablename__ = "asset_library_binding"
    __table_args__ = (
        UniqueConstraint("credential_type", "credential_id", name="uq_asset_library_binding_credential"),
        Index("ix_asset_library_binding_account_id", "account_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("asset_library_account.id", ondelete="CASCADE"), nullable=False
    )
    credential_type: Mapped[str] = mapped_column(String(16), nullable=False)
    credential_id: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
