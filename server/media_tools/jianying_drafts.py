"""Host-neutral Jianying draft tool: check the edit timeline, then export it on the local render lane.

The check runs before anything is queued, so blocking issues and an unavailable narration version are
refused without creating a batch; the render task checks again when it starts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from lib.edit_timeline.errors import EditTimelineError
from lib.generation.generation_batch import GenerationBatchReadModel
from lib.jianying_draft.basis import WITH_NARRATION, WITHOUT_NARRATION, DraftNarration
from lib.jianying_draft.errors import JianyingDraftError
from lib.jianying_draft.results import JianyingDraftRender
from server.agent_toolset.envelope import json_value
from server.media_tools.context import tool_error, tool_problem
from server.media_tools.render_submission import submit_render_task
from server.services.tasks.render_tasks import jianying_draft_task_request
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolProblem, ToolRequest

_OPERATION = "export_jianying_draft"


class ExportJianyingDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeline: str = Field(min_length=1, description="剪辑时间线 ID（如 tl-3f9a0c21），取自 list_timelines 的结果")
    revision: int | SkipJsonSchema[None] = Field(
        default=None,
        ge=1,
        description="导出哪个修订；省略时取导出任务开始时的最新修订",
    )
    narration: DraftNarration = Field(
        default=WITHOUT_NARRATION,
        description="旁白版本：without_narration 不带旁白轨；with_narration 带旁白轨，只对 TTS 配音项目开放",
    )


class JianyingDraftToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    jianying_draft: JianyingDraftRender
    batch_id: str


type JianyingDraftToolValue = JianyingDraftToolResult | GenerationBatchReadModel


async def export_jianying_draft(
    request: ToolRequest[ExportJianyingDraftRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[JianyingDraftToolValue]:
    # 剪映草稿服务依赖 pyJianYingDraft 与呈现模型读侧，调用时才导入。
    from server.services.presentation.timeline_jianying_draft import TimelineJianyingDraftService

    value = request.value
    try:
        check = await TimelineJianyingDraftService(services.projects).check(
            scope.project_name, value.timeline, narration=value.narration, revision=value.revision
        )
        submission = await submit_render_task(
            jianying_draft_task_request(
                episode=check.episode,
                timeline_id=check.timeline_id,
                revision=value.revision,
                narration=value.narration,
            ),
            operation=_OPERATION,
            scope=scope,
            caller=caller,
            services=services,
        )
    except (JianyingDraftError, EditTimelineError) as exc:
        return tool_problem(str(exc), code=exc.code, params=exc.params or None)
    except Exception as exc:
        return tool_error(_OPERATION, exc)
    if isinstance(submission, ToolProblem):
        return ToolOutcome(problem=submission)
    if submission.problem is not None:
        return ToolOutcome(problem=submission.problem)
    if submission.result is None:
        return ToolOutcome(value=submission.batch)
    return ToolOutcome(
        value=JianyingDraftToolResult(
            jianying_draft=JianyingDraftRender.model_validate(submission.result["jianying_draft"]),
            batch_id=submission.batch.batch_id,
        )
    )


def jianying_draft_projection(value: JianyingDraftToolValue) -> dict[str, Any]:
    if isinstance(value, GenerationBatchReadModel):
        return {"generation_batch": json_value(value)}
    return json_value(value)


def jianying_draft_summary(value: JianyingDraftToolValue) -> str | None:
    if isinstance(value, GenerationBatchReadModel):
        return None
    draft = value.jianying_draft
    label = "带旁白" if draft.narration == WITH_NARRATION else "不带旁白"
    warnings = f"；警告 {len(draft.warnings)} 条" if draft.warnings else ""
    return (
        f"剪映草稿已导出：剪辑时间线 {draft.timeline_id} 修订 {draft.revision}，{label}版本，第 {draft.version} 版，"
        f"时长 {draft.duration} 秒{warnings}。创作者在 Web 端填写本机剪映草稿目录后下载。"
    )


__all__ = [
    "ExportJianyingDraftRequest",
    "JianyingDraftToolResult",
    "export_jianying_draft",
    "jianying_draft_projection",
    "jianying_draft_summary",
]
