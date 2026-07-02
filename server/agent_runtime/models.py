"""Agent runtime data models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SessionStatus = Literal["idle", "running", "completed", "error", "interrupted", "closed"]


@dataclass(frozen=True, slots=True)
class ReplayBatch:
    """会话消息流的首个事件：订阅瞬间缓冲区内的历史消息，一次性交付。

    replay 关闭时仍作为首个事件产出（messages 为空），协议形态对消费方保持统一。
    """

    messages: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class LiveMessage:
    """会话消息流的直播事件：回放边界之后逐条广播的消息。"""

    message: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """会话消息流的心跳事件：idle_timeout 内无消息时产出。

    消费方在其上执行存活自检（SSE 查断线、同步收集方查 deadline/会话状态），
    保证空闲期也有确定性的醒来时机（见 ADR-0005）。
    """


SessionStreamEvent = ReplayBatch | LiveMessage | Heartbeat
"""``SessionManager.stream_messages`` 产出的语义化事件。

序列协议：ReplayBatch（恰好一次、必为首个）→ LiveMessage / Heartbeat 交错；
订阅队列溢出以流结束表达，流结束即重连信号，无专门事件。
"""


class SessionMeta(BaseModel):
    """Session metadata stored in database."""

    id: str  # 对外暴露，填充 sdk_session_id 值
    project_name: str
    title: str = ""
    status: SessionStatus = "idle"
    created_at: datetime
    updated_at: datetime


class AssistantSnapshotV2(BaseModel):
    """Unified assistant snapshot for history and reconnect."""

    session_id: str
    status: SessionStatus
    turns: list[dict[str, Any]]
    draft_turn: dict[str, Any] | None = None
    pending_questions: list[dict[str, Any]] = Field(default_factory=list)
