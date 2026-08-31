"""SessionStore ORM models — SDK transcript mirror tables."""

from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Index, PrimaryKeyConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin, UserOwnedMixin


class AgentSessionEntry(TimestampMixin, UserOwnedMixin, Base):
    """SDK transcript mirror — one row per SessionStoreEntry."""

    __tablename__ = "agent_session_entries"

    project_key: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    subpath: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uuid: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    mtime_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("project_key", "session_id", "subpath", "seq"),
        Index(
            "uq_agent_entries_uuid",
            "project_key",
            "session_id",
            "subpath",
            "uuid",
            unique=True,
            postgresql_where=text("uuid IS NOT NULL"),
            sqlite_where=text("uuid IS NOT NULL"),
        ),
        Index(
            "idx_agent_entries_listing",
            "project_key",
            "session_id",
            "mtime_ms",
        ),
    )


class AgentSessionSummary(TimestampMixin, UserOwnedMixin, Base):
    """Per-session summary maintained by SDK fold_session_summary()."""

    __tablename__ = "agent_session_summaries"

    project_key: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    mtime_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)


def register_models() -> None:
    """把 agent_session 相关 ORM 模型登记到 ``Base.metadata``。

    登记发生在 import 本模块时（模型类定义即注册），本函数不做额外工作；
    调用它是为了让「因副作用而 import」在调用点显式可见。
    """
