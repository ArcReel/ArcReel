"""剪映草稿导出的结果形态：导出前检查、登记下来的草稿与产物现状；服务、HTTP 与 Agent 工具共用。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from lib.artifacts.artifact_manifest import ArtifactStatus
from lib.edit_timeline.readout import TimelineIssue
from lib.jianying_draft.basis import DraftNarration


class JianyingDraftCheck(BaseModel):
    """导出前检查通过：将要导出的修订，以及不阻断导出的 issues。"""

    model_config = ConfigDict(frozen=True)

    episode: int
    timeline_id: str
    revision: int
    narration: DraftNarration
    duration: float
    warnings: tuple[TimelineIssue, ...]


class JianyingDraftRender(BaseModel):
    """一次导出登记下来的剪映草稿。"""

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
    """一个剪映草稿产物身份的现状；``status`` 为 missing 时没有版本与导出时间。"""

    model_config = ConfigDict(frozen=True)

    episode: int
    timeline_id: str
    narration: DraftNarration
    status: ArtifactStatus
    artifact_path: str
    version: int | None
    rendered_at: str | None


__all__ = ["JianyingDraftCheck", "JianyingDraftRender", "JianyingDraftStatus"]
