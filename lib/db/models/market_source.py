"""市场源 ORM 模型：全局实体，无 user 维度。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class MarketSource(TimestampMixin, Base):
    """一个登记在本实例的市场源。

    ``cached_index`` 是最近一次成功抓取的索引原文，``fetched_at`` 是最近一次成功刷新（含 304）
    的时间；刷新失败只改 ``status`` / ``last_error``，两者保留。
    """

    __tablename__ = "market_source"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    address: Mapped[str] = mapped_column(String(2048), nullable=False)
    index_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_index: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
