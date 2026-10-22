"""官方同步取回的目录数据与同步状态。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class CatalogOfficialSync(TimestampMixin, Base):
    """官方同步的状态，只有一行（docs/adr/0103）。

    ``catalog`` 是最近一次应用的数据文件（目录数据格式的 JSON），已补上上游删除、本地保留最后一个版本的条目；
    抓取时已核对清单文件里的哈希，读入时只过实例侧解析。从未成功取回数据时 ``dataset_version`` 与 ``catalog``
    为空。``last_success_at`` 是上次检查成功（含数据集版本未更新）的时间，``last_error`` 是最近一次检查失败的原因，
    检查成功后清空。
    """

    __tablename__ = "catalog_official_sync"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    dataset_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    catalog: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
