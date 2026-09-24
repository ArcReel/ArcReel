"""Provider credential ORM model."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin
from lib.infra.data_root_layout import DataRootLayout


class ProviderCredential(TimestampMixin, Base):
    """供应商凭证。每个供应商可有多条凭证，其中最多一条 is_active=True。"""

    __tablename__ = "provider_credential"
    __table_args__ = (
        Index("ix_provider_credential_provider", "provider"),
        Index(
            "uq_provider_credential_one_active",
            "provider",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    credentials_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 按 registry key 命名的定型列，承载需要两个 secret 字符串的内置 provider（可灵 Kling 的
    # access_key + secret_key，JWT HS256 鉴权）。除可灵外恒为 NULL 的稀疏列（见 ADR 0037）。
    access_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def credentials_file(self) -> str | None:
        """凭证文件位置：Vertex 凭证由 id 经数据根布局推导；推导位置没有文件时回退到记录的路径。"""
        if self.provider == "gemini-vertex":
            derived = DataRootLayout.current().vertex_credential_path(self.id)
            if derived.is_file():
                return str(derived)
        return self.credentials_path

    def overlay_config(self, config: dict[str, str]) -> dict[str, str]:
        """将凭证字段合并到配置字典中，返回修改后的 config。

        列名即 config dict key（registry key 名 = 表单字段名 = backend 构造参数名 = config key
        全程同名，不引翻译层；见 ADR 0037）。
        """
        if self.api_key:
            config["api_key"] = self.api_key
        if credentials_file := self.credentials_file():
            config["credentials_path"] = credentials_file
        if self.base_url:
            config["base_url"] = self.base_url
        if self.access_key:
            config["access_key"] = self.access_key
        if self.secret_key:
            config["secret_key"] = self.secret_key
        return config
