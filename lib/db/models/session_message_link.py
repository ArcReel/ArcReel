"""用户消息身份映射 ORM 模型 — 事件日志条目 ↔ SDK transcript entry。"""

from __future__ import annotations

from sqlalchemy import PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin, UserOwnedMixin


class AgentSessionUserMessageLink(TimestampMixin, UserOwnedMixin, Base):
    """ArcReel 合成的用户消息 id ↔ SDK transcript entry uuid 的映射。

    事件日志（agent_session_event_log）与 transcript 镜像（agent_session_entries）
    是两套独立的 id 体系，同一条用户消息在两边各有身份。本表把二者的对应关系
    固化下来，供分支会话按事件日志条目定位 transcript 中的截断位置。

    映射在 SDK 回放用户消息副本时写入，缺失即视为不可定位：没有映射行的会话
    读取路径照常工作。
    """

    __tablename__ = "agent_session_user_message_links"

    session_id: Mapped[str] = mapped_column(String, nullable=False)
    # 事件日志用户条目 payload 里的 uuid（形如 user-<hex>）。
    user_entry_uuid: Mapped[str] = mapped_column(String, nullable=False)
    # agent_session_entries.uuid：该用户消息在 SDK transcript 中的 entry 身份。
    sdk_entry_uuid: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (PrimaryKeyConstraint("session_id", "user_entry_uuid"),)
