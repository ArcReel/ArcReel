"""Agent runtime data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

SessionStatus = Literal["idle", "running", "completed", "error", "interrupted", "closed"]


@dataclass(frozen=True, slots=True)
class SubscriptionReady:
    """The first session-stream event: a barrier marking atomic subscription establishment.

    After receiving it, consumers can rely on gap-free live broadcasts. The entry stream uses this boundary to
    backfill stored entries before consuming live messages, filtering duplicates using a seq threshold (identity comparison)."""


@dataclass(frozen=True, slots=True)
class LiveMessage:
    """A live session-stream event: a message broadcast after the subscription barrier."""

    message: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """A session-stream heartbeat, emitted when no message arrives within idle_timeout.

    Consumers use it for liveness checks (SSE disconnection checks, synchronous collectors checking deadlines/session state),
    ensuring deterministic wakeups during idle periods too (ADR-0005)."""


QueuedMessageState = Literal["queued", "unsent"]
"""Queued-message state. ``queued``: delivered to the CLI and waiting in its queue for inclusion in a turn.
``unsent``: still queued and unprocessed when the CLI exited; resend to the CLI only when the user clicks Send, never automatically."""

WithdrawalIntent = Literal["edit", "delete"]
"""User intent when withdrawing a queued message: ``edit`` returns content to the input box; ``delete`` discards it."""


@dataclass(slots=True)
class QueuedMessage:
    """A queued user message delivered to the CLI but not yet accepted into the Agent conversation; server-memory state cleared with the session.

    ``id`` is the accepted user entry’s uuid and remains unchanged throughout the message lifecycle. ``cli_uuid`` is the uuid
    sent with the message to the CLI; CLI ``command_lifecycle`` frames and user-message replay identify the message by it."""

    entry: dict[str, Any]
    """The user entry written to the session event log when accepted."""
    cli_uuid: str
    content: str | list[dict[str, Any]] = ""
    """Message content delivered to the CLI. Resend unchanged when resending an unsent message, sending immediately, or requeueing after CLI discard."""
    client_key: str | None = None
    state: QueuedMessageState = "queued"
    withdrawal: WithdrawalIntent | Literal["send_now"] | None = None
    """User withdrawal intent; ``send_now`` means immediate sending: resubmit with ``now`` priority after withdrawal.
    Retain it after the CLI reports withdrawal failure; if the CLI later reports cancelled, finalize according to this intent."""
    withdrawing: bool = False
    """The withdrawal request is awaiting a CLI response."""
    withdrawn: bool = False
    """Removed from the queue at the user’s withdrawal request."""
    id: str = field(init=False)

    def __post_init__(self) -> None:
        self.id = str(self.entry["uuid"])

    def to_payload(self) -> dict[str, Any]:
        """External shape shared by send responses and queued-message events in the entry stream."""
        return {
            "id": self.id,
            "content": self.entry.get("content", []),
            "timestamp": self.entry.get("timestamp"),
            "state": self.state,
        }


SessionStreamEvent = SubscriptionReady | LiveMessage | Heartbeat
"""Semantic events emitted by ``SessionManager.stream_messages``.

Sequence protocol: SubscriptionReady (exactly once, always first) → interleaved LiveMessage / Heartbeat;
subscription queue overflow ends the stream. Stream termination signals reconnection, without a dedicated event.
"""


class SessionMeta(BaseModel):
    """Session metadata stored in database."""

    id: str  # Exposed externally, populated with sdk_session_id.
    project_name: str
    title: str = ""
    status: SessionStatus = "idle"
    superseded_by: str | None = None
    """Nonempty means a branched session superseded this one; the value is the new session’s sdk_session_id."""
    fork_parent_session_id: str | None = None
    """Nonempty means this session was forked; the value is the original session’s sdk_session_id."""
    fork_anchor_uuid: str | None = None
    """The fork anchor’s entry uuid in the transcript domain, written together with fork_parent_session_id."""
    created_at: datetime
    updated_at: datetime
