"""Host-neutral final-cut tool: check the edit timeline, then render it on the local render lane.

The check runs before anything is queued, so blocking issues and unsupported content are refused
without creating a batch; the render task checks again when it starts.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from lib.edit_timeline.errors import EditTimelineError
from lib.final_cut.basis import DEFAULT_VARIANT
from lib.final_cut.errors import FinalCutError
from lib.final_cut.service import FinalCutRender, FinalCutService
from lib.generation.generation_batch import GenerationBatchReadModel
from server.agent_toolset.envelope import json_value
from server.media_tools.context import tool_error, tool_problem
from server.media_tools.render_submission import submit_render_task
from server.services.tasks.render_tasks import final_cut_task_request
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolProblem, ToolRequest

_OPERATION = "render_final_cut"


def final_cut_download_url(project_name: str, artifact_path: str, version: int) -> str:
    """成片的下载地址；``v`` 随版本变化，旧版本的缓存不会遮住新文件。"""
    return f"/api/v1/files/{quote(project_name, safe='')}/{quote(artifact_path)}?v={version}"


class RenderFinalCutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeline: str = Field(min_length=1, description="剪辑时间线 ID（如 tl-3f9a0c21），取自 list_timelines 的结果")
    revision: int | SkipJsonSchema[None] = Field(
        default=None,
        ge=1,
        description="渲染哪个修订；省略时取提交时的最新修订",
    )


class FinalCutToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    final_cut: FinalCutRender
    download_url: str
    batch_id: str


type FinalCutToolValue = FinalCutToolResult | GenerationBatchReadModel


async def render_final_cut(
    request: ToolRequest[RenderFinalCutRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[FinalCutToolValue]:
    try:
        check = await FinalCutService(services.projects).check(
            scope.project_name, request.value.timeline, revision=request.value.revision, variant=DEFAULT_VARIANT
        )
        submission = await submit_render_task(
            final_cut_task_request(
                episode=check.episode,
                timeline_id=check.timeline_id,
                revision=check.revision,
                variant=DEFAULT_VARIANT,
            ),
            operation=_OPERATION,
            scope=scope,
            caller=caller,
            services=services,
        )
    except (FinalCutError, EditTimelineError) as exc:
        return tool_problem(str(exc), code=exc.code, params=exc.params or None)
    except Exception as exc:
        return tool_error(_OPERATION, exc)
    if isinstance(submission, ToolProblem):
        return ToolOutcome(problem=submission)
    if submission.problem is not None:
        return ToolOutcome(problem=submission.problem)
    if submission.result is None:
        return ToolOutcome(value=submission.batch)
    final_cut = FinalCutRender.model_validate(submission.result["final_cut"])
    return ToolOutcome(
        value=FinalCutToolResult(
            final_cut=final_cut,
            download_url=final_cut_download_url(scope.project_name, final_cut.artifact_path, final_cut.version),
            batch_id=submission.batch.batch_id,
        )
    )


def final_cut_projection(value: FinalCutToolValue) -> dict[str, Any]:
    if isinstance(value, GenerationBatchReadModel):
        return {"generation_batch": json_value(value)}
    return json_value(value)


def final_cut_summary(value: FinalCutToolValue) -> str | None:
    if isinstance(value, GenerationBatchReadModel):
        return None
    final_cut = value.final_cut
    acceptance = final_cut.acceptance
    warnings = f"；警告 {len(final_cut.warnings)} 条" if final_cut.warnings else ""
    return (
        f"成片已渲染：剪辑时间线 {final_cut.timeline_id} 修订 {final_cut.revision}，第 {final_cut.version} 版，"
        f"时长 {acceptance.video_duration} 秒（剪辑时间线 {acceptance.expected_duration} 秒）{warnings}。"
        f"下载：{value.download_url}"
    )


__all__ = [
    "FinalCutToolResult",
    "RenderFinalCutRequest",
    "final_cut_download_url",
    "final_cut_projection",
    "final_cut_summary",
    "render_final_cut",
]
