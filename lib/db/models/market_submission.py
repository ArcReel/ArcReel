"""分享提交记录：本地端点与官方服务提交令牌的绑定，以及最近一次取回的状态。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, utc_now


class MarketSubmission(Base):
    """每个端点只记最近一次提交；再次提交以官方服务返回的令牌覆盖。"""

    __tablename__ = "market_submission"

    custom_endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("custom_endpoint.id", ondelete="CASCADE"), primary_key=True
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    #: ``open`` / ``merged`` / ``closed``，官方服务不可达时保留上次取回的值。
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    pr_url: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
