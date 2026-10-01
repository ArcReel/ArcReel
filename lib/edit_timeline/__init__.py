"""剪辑时间线：一集的一套具名剪辑决策，成片与剪映草稿的唯一真相源（``docs/adr/0090``）。"""

from lib.edit_timeline.errors import EditTimelineError, EditTimelineErrorCode
from lib.edit_timeline.model import RevisionAuthor
from lib.edit_timeline.readout import (
    EditTimelineReadout,
    IssueCode,
    IssueScope,
    IssueSeverity,
    TimelineIssue,
)
from lib.edit_timeline.service import (
    ConcurrentRevision,
    EditTimelineService,
    EditTimelineWriteResult,
    RevisionHistory,
    RevisionSummary,
    TimelineSummary,
)

__all__ = [
    "ConcurrentRevision",
    "EditTimelineError",
    "EditTimelineErrorCode",
    "EditTimelineReadout",
    "EditTimelineService",
    "EditTimelineWriteResult",
    "IssueCode",
    "IssueScope",
    "IssueSeverity",
    "RevisionAuthor",
    "RevisionHistory",
    "RevisionSummary",
    "TimelineIssue",
    "TimelineSummary",
]
