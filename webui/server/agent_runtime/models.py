"""Agent runtime data models."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SessionStatus = Literal["idle", "running", "completed", "error", "interrupted"]


class SessionMeta(BaseModel):
    """Session metadata stored in SQLite."""
    id: str
    sdk_session_id: Optional[str] = None
    project_name: str
    title: str = ""
    status: SessionStatus = "idle"
    created_at: str
    updated_at: str


class AssistantSnapshotV2(BaseModel):
    """Unified assistant snapshot for history and reconnect."""

    session_id: str
    status: SessionStatus
    turns: list[dict[str, Any]]
    draft_turn: Optional[dict[str, Any]] = None
    pending_questions: list[dict[str, Any]] = Field(default_factory=list)
