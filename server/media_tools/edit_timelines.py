"""Host-neutral edit-timeline tools: create (from script or copied from a timeline), list, read, batch edit,
rename and revision history / restore.

Each handler is a thin adapter over the lib edit-timeline command; domain errors keep their
stable codes as tool problems.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lib.edit_timeline import (
    EditTimelineError,
    EditTimelineReadout,
    EditTimelineService,
    EditTimelineWriteResult,
    IssueSeverity,
    RevisionAuthor,
    RevisionHistory,
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

    source: Literal["script", "timeline"] = Field(
        alias="from",
        description=(
            "新建方式。script：按当前脚本顺序排列每个视频单元，整段使用、全部硬切，需要 episode；"
            "timeline：复制一条已有剪辑时间线（需要 timeline，可选 revision），新时间线落在同一集"
        ),
    )
    name: str = Field(
        min_length=1,
        max_length=TIMELINE_NAME_MAX_LENGTH,
        description="显示名，同一集内不能重名，写成创作者一眼认得出的剪法，如「完整版」「快节奏版」",
    )
    episode: PositiveEpisode | None = Field(
        default=None, description="集的集 ID（项目详情 episodes[].episode），不是第几集；from=script 时必填"
    )
    timeline: str | None = Field(
        default=None,
        min_length=1,
        description=f"被复制的剪辑时间线 ID；from=timeline 时必填。{_TIMELINE_ID_DESCRIPTION}",
    )
    revision: int | None = Field(
        default=None,
        ge=1,
        description="被复制的修订号，取自 list_revisions；省略时复制最新修订。只在 from=timeline 时可用",
    )

    @model_validator(mode="after")
    def _arguments_match_source(self) -> CreateTimelineRequest:
        if self.source == "script":
            if self.episode is None:
                raise ValueError("from=script 时必须给出 episode")
            if self.timeline is not None or self.revision is not None:
                raise ValueError("timeline、revision 只用于 from=timeline")
        else:
            if self.timeline is None:
                raise ValueError("from=timeline 时必须给出 timeline")
            if self.episode is not None:
                raise ValueError("from=timeline 时集取自源时间线，省略 episode")
        return self


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


class RenameTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeline: str = Field(min_length=1, description=_TIMELINE_ID_DESCRIPTION)
    name: str = Field(
        min_length=1,
        max_length=TIMELINE_NAME_MAX_LENGTH,
        description="新的显示名，同一集内不能重名",
    )


class ListRevisionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeline: str = Field(min_length=1, description=_TIMELINE_ID_DESCRIPTION)


class RestoreRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeline: str = Field(min_length=1, description=_TIMELINE_ID_DESCRIPTION)
    revision: int = Field(ge=1, description="要回滚到的修订号，取自 list_revisions")


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
    value = request.value
    service = EditTimelineService(services.projects)
    try:
        if value.source == "timeline":
            assert value.timeline is not None
            readout = await service.copy(
                scope.project_name,
                value.timeline,
                name=value.name,
                revision=value.revision,
                author=_author(caller),
                agent_turn=caller.current_agent_turn(),
            )
        else:
            assert value.episode is not None
            readout = await service.create_from_script(
                scope.project_name,
                episode=value.episode,
                name=value.name,
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


async def rename_timeline(
    request: ToolRequest[RenameTimelineRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[TimelineSummary]:
    try:
        summary = await EditTimelineService(services.projects).rename(
            scope.project_name, request.value.timeline, name=request.value.name
        )
    except EditTimelineError as exc:
        return _domain_problem(exc)
    except Exception as exc:
        return tool_error("rename_timeline", exc)
    return ToolOutcome(value=summary)


async def list_revisions(
    request: ToolRequest[ListRevisionsRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[RevisionHistory]:
    try:
        history = await EditTimelineService(services.projects).list_revisions(
            scope.project_name, request.value.timeline
        )
    except EditTimelineError as exc:
        return _domain_problem(exc)
    except Exception as exc:
        return tool_error("list_revisions", exc)
    return ToolOutcome(value=history)


async def restore_revision(
    request: ToolRequest[RestoreRevisionRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[EditTimelineWriteResult]:
    value = request.value
    try:
        result = await EditTimelineService(services.projects).restore(
            scope.project_name,
            value.timeline,
            revision=value.revision,
            author=_author(caller),
            agent_turn=caller.current_agent_turn(),
        )
    except EditTimelineError as exc:
        return _domain_problem(exc)
    except Exception as exc:
        return tool_error("restore_revision", exc)
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
        f"剪辑时间线「{timeline.name}」（{timeline.id}）属于集（id={timeline.episode}），修订 {readout.revision}："
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
        f"- {item.id} 集（id={item.episode}）「{item.name}」修订 {item.revision}，{item.clip_count} 个剪辑片段，"
        f"最近修改 {item.updated_at}"
        for item in timelines
    )


def timeline_renamed_summary(summary: TimelineSummary) -> str:
    return f"剪辑时间线 {summary.id}（集 id={summary.episode}）已改名为「{summary.name}」，剪辑内容与修订不变"


def revision_history_summary(history: RevisionHistory) -> str:
    timeline = history.timeline
    lines = [
        f"剪辑时间线「{timeline.name}」（{timeline.id}，集 id={timeline.episode}）共 {len(history.revisions)} 个修订，"
        f"最新修订 {history.latest_revision}"
    ]
    for revision in history.revisions:
        restored = f"（回滚到修订 {revision.restored_from}）" if revision.restored_from is not None else ""
        lines.append(
            f"- 修订 {revision.number}{restored} {revision.created_at} {revision.author.kind}：{revision.summary}"
        )
    return "\n".join(lines)


__all__ = [
    "CreateTimelineRequest",
    "EditTimelineRequest",
    "ListRevisionsRequest",
    "ListTimelinesRequest",
    "ReadTimelineRequest",
    "RenameTimelineRequest",
    "RestoreRevisionRequest",
    "create_timeline",
    "edit_timeline",
    "list_revisions",
    "list_timelines",
    "read_timeline",
    "rename_timeline",
    "restore_revision",
    "revision_history_summary",
    "timeline_list_summary",
    "timeline_readout_summary",
    "timeline_renamed_summary",
    "timeline_write_summary",
]
