"""自定义调用端点 ORM model。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class CustomEndpoint(TimestampMixin, Base):
    """用户导入或手工创建的调用端点定义。

    ``definition`` 整份 JSON 是唯一真相源；其余列都是写入时由定义派生的只读镜像，供 DB 层
    在不解析 JSON 的前提下过滤与排序（``docs/adr/0067``）。镜像列不接受独立编辑——改名即改
    ``meta.name``，改协议即改定义本体。

    全局实体，无 user 维度（与 :class:`~lib.db.models.custom_provider.CustomProvider` 一致）。
    ``display_name`` 不加唯一约束：重复导入是提示而非拒绝，血统按 ``meta.author + meta.name``
    判定并由客户端处置。
    """

    __tablename__ = "custom_endpoint"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # 容器类型，取自 definition.kind（当前只有 declarative；Python 插件是独立议题的预留槽位）
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # 定义遵循的格式版本，原样保留文件里的值、不改写，也不做迁移
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # 运行时按格式判定，首期恒 "video"；格式本身不含该字段
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)  # ← definition.meta.name
