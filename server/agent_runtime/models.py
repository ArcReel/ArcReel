"""Agent runtime data models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

SessionStatus = Literal["idle", "running", "completed", "error", "interrupted", "closed"]


@dataclass(frozen=True, slots=True)
class SubscriptionReady:
    """会话消息流的首个事件：订阅已原子建立的屏障标记。

    消费方消费到该事件后，可确信其后的直播广播无缝隙——entry 流以此为界
    先补库读存量条目，再消费直播消息，重复由 seq 门槛过滤（身份比对）。
    """


@dataclass(frozen=True, slots=True)
class LiveMessage:
    """会话消息流的直播事件：订阅屏障之后逐条广播的消息。"""

    message: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """会话消息流的心跳事件：idle_timeout 内无消息时产出。

    消费方在其上执行存活自检（SSE 查断线、同步收集方查 deadline/会话状态），
    保证空闲期也有确定性的醒来时机（见 ADR-0005）。
    """


SessionStreamEvent = SubscriptionReady | LiveMessage | Heartbeat
"""``SessionManager.stream_messages`` 产出的语义化事件。

序列协议：SubscriptionReady（恰好一次、必为首个）→ LiveMessage / Heartbeat 交错；
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
