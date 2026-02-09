"""
Agent runtime data models.
"""

from typing import Literal, Optional

from pydantic import BaseModel

SessionStatus = Literal["idle", "running", "completed", "error", "interrupted"]


class SessionMeta(BaseModel):
    """Session metadata stored in SQLite."""
    id: str
    sdk_session_id: Optional[str] = None
    project_name: str
    title: str = ""
    status: SessionStatus = "idle"
    transcript_path: Optional[str] = None
    created_at: str
    updated_at: str
