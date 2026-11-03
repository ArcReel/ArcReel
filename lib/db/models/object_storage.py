"""对象存储连接、中转副本与正式素材对象 ORM model。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin

#: 当前连接的 ``slot`` 值；唯一索引让实例同一时刻只有一条当前连接。
CURRENT_CONNECTION_SLOT = 1


class ObjectStorageConnection(TimestampMixin, Base):
    """用户自有对象存储的连接配置，独立于媒体生成供应商的凭证。

    首期只有 TOS 一种 ``kind``，实例至多一条当前连接：``slot`` 为 1 且唯一，并发首次保存也只能建出一条。
    ``slot`` 为空的是退役连接，只在迁移合并历史重复记录时为保留仍被引用的定位而留下，不作为当前连接读出。
    ``access_key_id`` / ``secret_access_key``
    明文落库，读出 API 时经 ``mask_secret`` 掩码，与供应商凭证同一规则。删除的连接 ID 不回收：
    之后保存在对象存储里的内容按连接 ID 记录所属连接，复用 ID 会让旧定位指向另一个桶。
    """

    __tablename__ = "object_storage_connection"
    __table_args__ = (
        Index("uq_object_storage_connection_slot", "slot", unique=True),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    access_key_id: Mapped[str] = mapped_column(Text, nullable=False)
    secret_access_key: Mapped[str] = mapped_column(Text, nullable=False)
    slot: Mapped[int | None] = mapped_column(Integer, nullable=True, default=CURRENT_CONNECTION_SLOT)


class RelayObject(TimestampMixin, Base):
    """为供应商取图写进用户对象存储的中转副本，与正式素材对象分开管理。

    记录在写入对象之前创建，写入中途失败也能找回定位。``connection_id`` / ``endpoint`` / ``bucket``
    是写入时的连接；连接之后被改成另一个桶时不删除，避免按同名 key 误删另一个桶里的对象。
    签名下载地址只交给供应商，不落库，清理只按这里的定位进行。

    ``state``：``retained`` 等待清理，``deleted`` 已删除。删除失败记下原因与下次重试时间。
    """

    __tablename__ = "object_storage_relay_object"
    __table_args__ = (
        Index("ix_object_storage_relay_object_state_next", "state", "next_attempt_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    # 使用这份副本的入库任务（provider_asset_link.id）；保留期据它的状态判断。
    link_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="retained")
    # 签发给供应商的下载地址到期时间；没有签发时为空。
    url_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delete_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FormalObject(TimestampMixin, Base):
    """保存在用户对象存储里的正式素材对象：远端内容版本的持久定位指向它。

    对象 key 按内容摘要命名（``arcreel-materials/`` 前缀，与中转副本分开），同一连接里同字节的内容版本共用一个
    对象，归属由 :class:`FormalObjectRef` 记录。记录在上传之前创建，上传中途失败或进程退出也能找回定位。

    ``state``：``uploading`` 已登记、远端尚未确认；``stored`` 远端已确认保存；``orphaned`` 本次提交新建、
    确认没有任何引用，等待删除（失败时记下原因与下次重试时间）；``deleted`` 已删除。
    ``created_attempt`` 是上传它的那次正式提交，失败补偿只删除本次提交新建的对象。
    """

    __tablename__ = "object_storage_formal_object"
    __table_args__ = (
        Index("uq_object_storage_formal_object_key", "connection_id", "bucket", "object_key", unique=True),
        Index("ix_object_storage_formal_object_state_next", "state", "next_attempt_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="uploading")
    created_attempt: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delete_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FormalObjectRef(TimestampMixin, Base):
    """一条对正式素材对象的引用：某引用方的一个内容版本使用它，或一次进行中的正式提交占用它。

    ``project_name`` 是引用方：项目名（项目整体删除后改挂墓碑名），或不是项目的消费者的带冒号键。
    ``state``：``pending`` 正式提交进行中（``attempt_id`` 是该提交），提交结算时按版本历史转为 ``active``
    或删除；``active`` 引用方正在使用内容版本 ``content_version_id``（项目里即版本历史中的版本）；``trashed``
    项目回收站里的删除记录仍可恢复出该内容版本。对象只要还有任何引用就不删除（见 ``docs/adr/0118``）。
    """

    __tablename__ = "object_storage_formal_object_ref"
    __table_args__ = (
        Index("ix_object_storage_formal_object_ref_object", "object_id"),
        Index("ix_object_storage_formal_object_ref_version", "project_name", "content_version_id"),
        Index("ix_object_storage_formal_object_ref_attempt", "attempt_id"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
