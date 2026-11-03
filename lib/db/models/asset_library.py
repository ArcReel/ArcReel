"""供应商素材库控制面账号、推理凭证绑定与供应商素材关联的 ORM model。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
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


class ProviderAssetLink(TimestampMixin, Base):
    """一个内容版本与供应商素材之间的关联，连同建立它的入库任务。

    关联属于 ``project_name`` 项目里的 ``content_version_id`` 内容版本：内容版本身份只在项目内唯一，
    归档导入会原样带上版本记录，所以键必须带项目。``account_identity`` / ``site`` / ``region`` /
    ``provider_project_name`` 是提交时的调用范围，账号之后被修改或删除也不重解释：账号记录可以原地换成
    另一个云账号的 Access Key，所以范围按冻结的云账号身份判断，不按 ``account_id``。``account_id`` 只记
    经哪条账号记录提交、之后经它查询状态；账号 ID 不回收，故不建外键。归档导入的关联不经本实例的账号记录，记为 0。取得供应商素材 ID 之前，记录已经
    存在并表示入库任务。

    每个内容版本在同一调用范围内至多一条 ``selected`` 记录，即当前选定的关联或入库任务；解除关联
    清掉 ``selected`` 并记下 ``unlinked_at``，之后的状态查询只更新事实，不恢复选定。
    """

    __tablename__ = "provider_asset_link"
    __table_args__ = (
        Index(
            "uq_provider_asset_link_selected",
            "project_name",
            "content_version_id",
            "account_identity",
            "site",
            "region",
            "provider_project_name",
            unique=True,
            sqlite_where=text("selected = 1"),
            postgresql_where=text("selected"),
        ),
        Index("ix_provider_asset_link_content_version", "project_name", "content_version_id"),
        Index("ix_provider_asset_link_next_check_at", "next_check_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # 发起时的资源与版本号，供展示与诊断；资源改名后不再准确，查找关联只按内容版本身份。
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 提交时内容版本的持久定位（项目内相对路径）；入库读取时按 content_digest 校验。
    locator_path: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)  # "upload" / "import"
    account_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # 提交或导入时账号所属云账号的身份摘要（lib.asset_library.bindings.account_identity），不含任何密钥。
    account_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    site: Mapped[str] = mapped_column(String(16), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_project_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # pending / submitting / processing / active / failed / uncertain，见 lib.asset_library.links
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_asset_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_group_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 提交入库时的素材名称，请求结果不确定时按它在素材组里查回素材 ID；导入的关联记录所选素材的名称。
    submit_name: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 最近一次状态查询自身失败的原因（网络、鉴权等），不改变入库状态。
    check_error: Mapped[str | None] = mapped_column(String(128), nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    unlinked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    check_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
