"""Host-neutral final-cut tool: check the edit timeline, then render it on the local render lane.

The check runs before anything is queued, so blocking issues and unsupported content are refused
without creating a batch; the render task checks again when it starts.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from lib.delivery.platforms import PLATFORMS, DeliveryCheck
from lib.edit_timeline.errors import EditTimelineError
from lib.final_cut.basis import BURNED_SUBTITLES, NO_SUBTITLES
from lib.final_cut.errors import FinalCutError
from lib.final_cut.service import FinalCutRender, FinalCutService
from lib.generation.generation_batch import GenerationBatchReadModel
from lib.i18n import _ as translate
from lib.jianying_draft.basis import DraftNarration
from server.agent_toolset.declaration import ToolImage
from server.agent_toolset.envelope import json_value
from server.media_tools.context import tool_error, tool_problem
from server.media_tools.render_submission import submit_render_task
from server.services.tasks.render_tasks import final_cut_task_request
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolProblem, ToolRequest

_OPERATION = "render_final_cut"

logger = logging.getLogger(__name__)


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
    narration: DraftNarration | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "旁白版本：without_narration 不混入旁白配音；with_narration 混入旁白配音，只对 TTS 配音项目开放。"
            "省略时 TTS 配音项目取 with_narration，其余取 without_narration"
        ),
    )
    burn_subtitles: bool = Field(default=True, description="是否把字幕烧进画面；false 得到不带字幕的干净画面")


class FinalCutToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    final_cut: FinalCutRender
    download_url: str
    batch_id: str
    preview_url: str | None = None
    """成片联系表的预览地址；没有联系表时没有。"""
    sheet: bytes | None = None
    """成片联系表图像字节；只随结果交给 Agent 看图，不进入结构化结果。"""


type FinalCutToolValue = FinalCutToolResult | GenerationBatchReadModel


async def render_final_cut(
    request: ToolRequest[RenderFinalCutRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[FinalCutToolValue]:
    value = request.value
    try:
        check = await FinalCutService(services.projects).check(
            scope.project_name,
            value.timeline,
            revision=value.revision,
            narration=value.narration,
            subtitles=BURNED_SUBTITLES if value.burn_subtitles else NO_SUBTITLES,
        )
        submission = await submit_render_task(
            final_cut_task_request(
                episode=check.episode,
                timeline_id=check.timeline_id,
                revision=check.revision,
                variant=check.variant,
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
    preview_url, sheet = await _sheet_payload(scope, services, final_cut)
    return ToolOutcome(
        value=FinalCutToolResult(
            final_cut=final_cut,
            download_url=final_cut_download_url(scope.project_name, final_cut.artifact_path, final_cut.version),
            batch_id=submission.batch.batch_id,
            preview_url=preview_url,
            sheet=sheet,
        )
    )


async def _sheet_payload(
    scope: ProjectScope, services: Services, final_cut: FinalCutRender
) -> tuple[str | None, bytes | None]:
    """成片联系表的预览地址与图像字节；没有联系表或文件读不出时都没有。"""
    sheet_path = final_cut.sheet_path
    if sheet_path is None:
        return None, None

    def _read() -> bytes:
        project_dir = services.projects.get_project_path(scope.project_name)
        return (project_dir / sheet_path).read_bytes()

    try:
        sheet = await asyncio.to_thread(_read)
    except OSError:
        logger.warning("成片联系表读不出：%s / %s", scope.project_name, sheet_path, exc_info=True)
        return None, None
    return final_cut_download_url(scope.project_name, sheet_path, final_cut.version), sheet


def final_cut_projection(value: FinalCutToolValue) -> dict[str, Any]:
    if isinstance(value, GenerationBatchReadModel):
        return {"generation_batch": json_value(value)}
    return json_value(value.model_dump(mode="json", exclude={"sheet"}))


def final_cut_images(value: FinalCutToolValue) -> Sequence[ToolImage]:
    if isinstance(value, GenerationBatchReadModel) or value.sheet is None:
        return []
    return [ToolImage(data=value.sheet, mime_type="image/jpeg")]


def _delivery_sentence(delivery: DeliveryCheck) -> str:
    """交付检查结论的一句话：点名平台，非通过行按文案键渲染（最多列 3 条）。"""
    name = translate(PLATFORMS[delivery.platform].name_key)
    details = [
        translate(row.hint_key, **row.hint_params)
        for row in delivery.rows
        if row.status != "pass" and row.hint_key is not None
    ]
    shown = "；".join(details[:3]) + (f" 等 {len(details)} 项" if len(details) > 3 else "")
    if delivery.fail_count:
        return translate("delivery_summary_fail", platform=name, count=delivery.fail_count, details=shown)
    if delivery.warn_count:
        return translate("delivery_summary_warn", platform=name, count=delivery.warn_count, details=shown)
    return translate("delivery_summary_pass", platform=name, count=len(delivery.rows))


def final_cut_summary(value: FinalCutToolValue) -> str | None:
    if isinstance(value, GenerationBatchReadModel):
        return None
    final_cut = value.final_cut
    acceptance = final_cut.acceptance
    warnings = f"；警告 {len(final_cut.warnings)} 条" if final_cut.warnings else ""
    loudness = (
        f"，响度 {acceptance.integrated_lufs:.1f} LUFS / 峰值 {acceptance.true_peak_dbtp:.1f} dBTP"
        if acceptance.integrated_lufs is not None and acceptance.true_peak_dbtp is not None
        else ""
    )
    loudness_warnings = f"；{'；'.join(acceptance.loudness_warnings)}" if acceptance.loudness_warnings else ""
    delivery = f"{_delivery_sentence(final_cut.delivery)}。" if final_cut.delivery is not None else ""
    sheet = f"。{translate('delivery_sheet_attached')}" if value.sheet is not None else ""
    return (
        f"成片已渲染：剪辑时间线 {final_cut.timeline_id} 修订 {final_cut.revision}，第 {final_cut.version} 版，"
        f"时长 {acceptance.video_duration} 秒（剪辑时间线 {acceptance.expected_duration} 秒）{loudness}{warnings}{loudness_warnings}。"
        f"下载：{value.download_url}{delivery}{sheet}"
    )


__all__ = [
    "FinalCutToolResult",
    "RenderFinalCutRequest",
    "final_cut_download_url",
    "final_cut_images",
    "final_cut_projection",
    "final_cut_summary",
    "render_final_cut",
]
