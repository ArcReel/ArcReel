"""市场安装记录：来源独立于端点定义与市场源的生命周期。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, utc_now


class MarketInstallation(Base):
    __tablename__ = "market_installation"
    __table_args__ = (UniqueConstraint("source_key", "slug"),)

    custom_endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("custom_endpoint.id", ondelete="CASCADE"), primary_key=True
    )
    source_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    installed_version: Mapped[str] = mapped_column(Text, nullable=False)
    installed_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
