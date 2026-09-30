"""Host-neutral edit-timeline tools: create from script, list, read and batch edit.

Each handler is a thin adapter over the lib edit-timeline command; domain errors keep their
stable codes as tool problems.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.edit_timeline import (
    EditTimelineError,
    EditTimelineReadout,
    EditTimelineService,
    EditTimelineWriteResult,
    IssueSeverity,
    RevisionAuthor,
    TimelineIssue,
    TimelineSummary,
)
from lib.edit_timeline.model import TIMELINE_NAME_MAX_LENGTH
from lib.edit_timeline.operations import TimelineOperation
from lib.edit_timeline.service import REVISION_SUMMARY_MAX_LENGTH
from server.draft_workflow import PositiveEpisode
from server.media_tools.context import tool_error, tool_problem
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest

_TIMELINE_ID_DESCRIPTION = "剪辑时间线 ID（如 tl-3f9a0c21），取自 list_timelines 或 create_timeline 的结果"


class CreateTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["script"] = Field(
        alias="from",
        description="新建方式。script：按当前脚本顺序排列每个视频单元，整段使用、全部硬切",
    )
    episode: PositiveEpisode = Field(description="集号，从 1 开始")
    name: str = Field(
        min_length=1,
        max_length=TIMELINE_NAME_MAX_LENGTH,
        description="显示名，同一集内不能重名，写成创作者一眼认得出的剪法，如「完整版」「快节奏版」",
    )


class ListTimelinesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode: PositiveEpisode | None = Field(default=None, description="只列这一集；省略时列出全部集")


class ReadTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeline: str = Field(min_length=1, description=_TIMELINE_ID_DESCRIPTION)


class EditTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeline: str = Field(min_length=1, description=_TIMELINE_ID_DESCRIPTION)
    base_revision: int = Field(ge=1, description="这批操作所依据的修订号，取自 read_timeline 的 revision")
    summary: str = Field(
        min_length=1,
        max_length=REVISION_SUMMARY_MAX_LENGTH,
        description="改动摘要，一句话写给创作者看，如「压低开场原声，把推门镜头提前」",
    )
    operations: list[TimelineOperation] = Field(min_length=1, description="按顺序执行的操作，整批原子提交")


def _author(caller: CallerContext) -> RevisionAuthor:
    kind = "arcreel_agent" if caller.source == "embedded" else "external_agent"
    return RevisionAuthor(kind=kind, user_id=caller.user_id)


def _domain_problem(exc: EditTimelineError) -> ToolOutcome[Any]:
    return tool_problem(str(exc), code=exc.code, params=exc.params or None)


async def create_timeline(
    request: ToolRequest[CreateTimelineRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[EditTimelineReadout]:
    try:
        readout = await EditTimelineService(services.projects).create_from_script(
            scope.project_name,
            episode=request.value.episode,
            name=request.value.name,
            author=_author(caller),
            agent_turn=caller.current_agent_turn(),
        )
    except EditTimelineError as exc:
        return _domain_problem(exc)
    except Exception as exc:
        return tool_error("create_timeline", exc)
    return ToolOutcome(value=readout)


async def list_timelines(
    request: ToolRequest[ListTimelinesRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[tuple[TimelineSummary, ...]]:
    try:
        timelines = await EditTimelineService(services.projects).list_timelines(
            scope.project_name, episode=request.value.episode
        )
    except EditTimelineError as exc:
        return _domain_problem(exc)
    except Exception as exc:
        return tool_error("list_timelines", exc)
    return ToolOutcome(value=timelines)


async def read_timeline(
    request: ToolRequest[ReadTimelineRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[EditTimelineReadout]:
    try:
        readout = await EditTimelineService(services.projects).read(scope.project_name, request.value.timeline)
    except EditTimelineError as exc:
        return _domain_problem(exc)
    except Exception as exc:
        return tool_error("read_timeline", exc)
    return ToolOutcome(value=readout)


async def edit_timeline(
    request: ToolRequest[EditTimelineRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[EditTimelineWriteResult]:
    value = request.value
    try:
        result = await EditTimelineService(services.projects).edit(
            scope.project_name,
            value.timeline,
            base_revision=value.base_revision,
            summary=value.summary,
            operations=value.operations,
            author=_author(caller),
            agent_turn=caller.current_agent_turn(),
        )
    except EditTimelineError as exc:
        return _domain_problem(exc)
    except Exception as exc:
        return tool_error("edit_timeline", exc)
    return ToolOutcome(value=result)


_SEVERITY_LABELS = {
    IssueSeverity.BLOCKING: "阻断",
    IssueSeverity.WARNING: "警告",
    IssueSeverity.INFO: "提示",
}


def _issue_counts(issues: tuple[TimelineIssue, ...]) -> str:
    return "、".join(
        f"{label} {sum(1 for issue in issues if issue.severity is severity)}"
        for severity, label in _SEVERITY_LABELS.items()
    )


def timeline_readout_summary(readout: EditTimelineReadout) -> str:
    timeline = readout.timeline
    return (
        f"剪辑时间线「{timeline.name}」（{timeline.id}，第 {timeline.episode} 集）修订 {readout.revision}："
        f"{len(readout.clips)} 个剪辑片段，总时长 {readout.duration} 秒；issues：{_issue_counts(readout.issues)}"
    )


def timeline_write_summary(result: EditTimelineWriteResult) -> str:
    changed = "、".join(clip.id for clip in result.clips) or "无"
    deleted = f"；删除 {'、'.join(result.deleted_clip_ids)}" if result.deleted_clip_ids else ""
    return (
        f"{result.message}\n受影响片段 {changed}{deleted}；总时长 {result.duration} 秒；"
        f"issues：{_issue_counts(result.issues)}"
    )


def timeline_list_summary(timelines: tuple[TimelineSummary, ...]) -> str:
    if not timelines:
        return "还没有剪辑时间线；用 create_timeline 按脚本新建一条"
    return "\n".join(
        f"- {item.id} 第 {item.episode} 集「{item.name}」修订 {item.revision}，{item.clip_count} 个剪辑片段，"
        f"最近修改 {item.updated_at}"
        for item in timelines
    )


__all__ = [
    "CreateTimelineRequest",
    "EditTimelineRequest",
    "ListTimelinesRequest",
    "ReadTimelineRequest",
    "create_timeline",
    "edit_timeline",
    "list_timelines",
    "read_timeline",
    "timeline_list_summary",
    "timeline_readout_summary",
    "timeline_write_summary",
]
