"""Jianying export result shapes shared by services, HTTP, and Agent tools: prechecks, registered drafts, and artifact status."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from lib.artifacts.artifact_manifest import ArtifactStatus
from lib.edit_timeline.readout import TimelineIssue
from lib.jianying_draft.basis import DraftNarration


class JianyingDraftCheck(BaseModel):
    """Passed pre-export check: the revision to export, plus issues that do not block export."""

    model_config = ConfigDict(frozen=True)

    episode: int
    timeline_id: str
    revision: int
    narration: DraftNarration
    duration: float
    warnings: tuple[TimelineIssue, ...]


class JianyingDraftRender(BaseModel):
    """A Jianying draft registered by one export."""

    model_config = ConfigDict(frozen=True)

    episode: int
    timeline_id: str
    revision: int
    narration: DraftNarration
    artifact_path: str
    version: int
    rendered_at: str
    duration: float
    warnings: tuple[TimelineIssue, ...]


class JianyingDraftStatus(BaseModel):
    """Current state of a Jianying-draft artifact identity; when ``status`` equals ``missing``, there is no version or export timestamp."""

    model_config = ConfigDict(frozen=True)

    episode: int
    timeline_id: str
    narration: DraftNarration
    status: ArtifactStatus
    artifact_path: str
    version: int | None
    rendered_at: str | None


__all__ = ["JianyingDraftCheck", "JianyingDraftRender", "JianyingDraftStatus"]
