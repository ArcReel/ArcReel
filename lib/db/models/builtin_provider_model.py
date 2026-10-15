"""内置供应商的供应商模型在本实例的状态。"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, Index, String, UniqueConstraint
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class BuiltinProviderModelState(TimestampMixin, Base):
    """内置供应商名下一个供应商模型的本地状态（docs/adr/0099）。

    一行是两种之一：

    - 官方收录模型的启用状态。模型清单与全部字段跟随官方数据，这里只记用户的启用或停用；没有行时，
      条目未下架即为启用。
    - 用户添加的模型（``user_added``）：API 模型名、媒体类型、供应商模型倍率、条目绑定与能力覆盖都由用户负责。
      官方数据日后收录同一模型键时，这一行只剩启用状态还在生效。

    ``model_id`` 是设置与项目里引用这个供应商模型的模型键：官方收录的取目录查询给出的键，用户添加的就是
    API 模型名。
    """

    __tablename__ = "builtin_provider_model"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_id", name="uq_builtin_provider_model"),
        Index("ix_builtin_provider_model_provider_id", "provider_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    user_added: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 以下只对用户添加的模型有意义；官方收录的模型忽略这些列。
    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default=sa_text("1"))
    # 条目绑定（docs/adr/0105）：「模型厂商/官方模型名」，媒体类型取 ``media_type``。NULL = 按名字现查。
    entry_binding: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 能力覆盖（docs/adr/0104），稀疏字典，键名与自定义供应商模型的能力覆盖一致。
    capability_overrides: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
