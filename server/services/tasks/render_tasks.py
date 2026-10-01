"""本地渲染车道的任务：请求构造、执行器分派与失败编码。

成片与剪映草稿的渲染任务以 ``render_`` 开头的任务类型入队到 ``render`` 车道。一个任务对应一个产物身份，
``resource_id`` 取该身份的编码，同一身份同一时刻只有一个活动任务；载荷就是请求本身，
不同请求撞上活动任务时报冲突而不去重。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from lib.edit_timeline.errors import EditTimelineError
from lib.final_cut.basis import SUPPORTED_VARIANTS, FinalCutVariant, final_cut_artifact_path, final_cut_key
from lib.final_cut.errors import FinalCutError
from lib.final_cut.service import FinalCutRender, FinalCutService
from lib.generation.generation_result import GenerationAction, GenerationProblem, encode_generation_problem
from lib.generation.render_lane import RENDER_MEDIA_TYPE
from lib.jianying_draft.basis import (
    WITH_NARRATION,
    WITHOUT_NARRATION,
    DraftNarration,
    jianying_draft_artifact_path,
    jianying_draft_key,
)
from lib.jianying_draft.errors import JianyingDraftError
from lib.project.project_manager import ProjectManager, get_project_manager

RENDER_FINAL_CUT_TASK_TYPE = "render_final_cut"
RENDER_JIANYING_DRAFT_TASK_TYPE = "render_jianying_draft"

_ACTIONS: dict[str, GenerationAction] = {
    "final_cut_render_failed": GenerationAction.RETRY,
    "final_cut_acceptance_failed": GenerationAction.RETRY,
    "final_cut_ffmpeg_unavailable": GenerationAction.NONE,
    "jianying_draft_hold_frame_unavailable": GenerationAction.RETRY,
    "jianying_draft_acceptance_failed": GenerationAction.RETRY,
}


def render_problem(exc: FinalCutError | JianyingDraftError | EditTimelineError) -> GenerationProblem:
    """渲染的领域错误保留稳定问题码：执行失败可重试，其余都要先改剪辑时间线或请求。"""
    return GenerationProblem(
        code=exc.code,
        detail=str(exc),
        action=_ACTIONS.get(exc.code, GenerationAction.FIX_INPUT),
        params=dict(exc.params),
    )


@dataclass(frozen=True, slots=True)
class RenderTaskRequest:
    """一个待入队的渲染任务：任务类型、产物身份与载荷。"""

    task_type: str
    resource_id: str
    artifact_key: str
    artifact_path: str
    payload: dict[str, Any]

    def enqueue_fields(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "media_type": RENDER_MEDIA_TYPE,
            "resource_id": self.resource_id,
            "payload": self.payload,
        }


def final_cut_resource_id(timeline_id: str, variant: FinalCutVariant) -> str:
    return f"{timeline_id}.{variant.slug}"


def jianying_draft_resource_id(timeline_id: str, narration: DraftNarration) -> str:
    return f"{timeline_id}.jianying_draft.{narration}"


def timeline_render_resource_ids(timeline_id: str) -> dict[str, list[str]]:
    """一条剪辑时间线可能占用的全部渲染任务身份，按任务类型分组。"""
    return {
        RENDER_FINAL_CUT_TASK_TYPE: [final_cut_resource_id(timeline_id, variant) for variant in SUPPORTED_VARIANTS],
        RENDER_JIANYING_DRAFT_TASK_TYPE: [
            jianying_draft_resource_id(timeline_id, narration) for narration in (WITHOUT_NARRATION, WITH_NARRATION)
        ],
    }


def final_cut_task_request(
    *, episode: int, timeline_id: str, revision: int | None, variant: FinalCutVariant
) -> RenderTaskRequest:
    key = final_cut_key(episode, timeline_id, variant)
    return RenderTaskRequest(
        task_type=RENDER_FINAL_CUT_TASK_TYPE,
        resource_id=final_cut_resource_id(timeline_id, variant),
        artifact_key=key.encode(),
        artifact_path=final_cut_artifact_path(episode, timeline_id, variant),
        payload={
            "timeline_id": timeline_id,
            "revision": revision,
            "narration": variant.narration,
            "subtitles": variant.subtitles,
        },
    )


def final_cut_task_result(result: FinalCutRender) -> dict[str, Any]:
    return {"file_path": result.artifact_path, "final_cut": result.model_dump(mode="json")}


def jianying_draft_task_request(
    *, episode: int, timeline_id: str, revision: int | None, narration: DraftNarration
) -> RenderTaskRequest:
    return RenderTaskRequest(
        task_type=RENDER_JIANYING_DRAFT_TASK_TYPE,
        resource_id=jianying_draft_resource_id(timeline_id, narration),
        artifact_key=jianying_draft_key(episode, timeline_id, narration).encode(),
        artifact_path=jianying_draft_artifact_path(episode, timeline_id, narration),
        payload={"timeline_id": timeline_id, "revision": revision, "narration": narration},
    )


type RenderTaskExecutor = Callable[[dict[str, Any], ProjectManager], Awaitable[dict[str, Any]]]


async def execute_final_cut_task(task: dict[str, Any], projects: ProjectManager) -> dict[str, Any]:
    payload: Mapping[str, Any] = task.get("payload") or {}
    variant = FinalCutVariant(narration=str(payload["narration"]), subtitles=str(payload["subtitles"]))
    revision = payload.get("revision")
    result = await FinalCutService(projects).render(
        str(task["project_name"]),
        str(payload["timeline_id"]),
        revision=revision if isinstance(revision, int) else None,
        variant=variant,
    )
    return final_cut_task_result(result)


async def execute_jianying_draft_task(task: dict[str, Any], projects: ProjectManager) -> dict[str, Any]:
    # 剪映草稿服务依赖 pyJianYingDraft 与呈现模型读侧，执行时才导入。
    from server.services.presentation.timeline_jianying_draft import TimelineJianyingDraftService

    payload: Mapping[str, Any] = task.get("payload") or {}
    revision = payload.get("revision")
    result = await TimelineJianyingDraftService(projects).render(
        str(task["project_name"]),
        str(payload["timeline_id"]),
        narration=payload["narration"],
        revision=revision if isinstance(revision, int) else None,
    )
    return {"file_path": result.artifact_path, "jianying_draft": result.model_dump(mode="json")}


RENDER_TASK_EXECUTORS: dict[str, RenderTaskExecutor] = {
    RENDER_FINAL_CUT_TASK_TYPE: execute_final_cut_task,
    RENDER_JIANYING_DRAFT_TASK_TYPE: execute_jianying_draft_task,
}
"""``render`` 车道的任务类型 → 执行器。"""


async def execute_render_task(task: dict[str, Any], *, projects: ProjectManager | None = None) -> dict[str, Any]:
    """执行一个渲染任务；领域错误按稳定问题码落库，读侧与 Agent 工具据此给出下一步。"""
    task_type = str(task.get("task_type"))
    executor = RENDER_TASK_EXECUTORS.get(task_type)
    if executor is None:
        raise ValueError(f"unsupported render task_type: {task_type}")
    try:
        return await executor(task, projects or get_project_manager())
    except (FinalCutError, JianyingDraftError, EditTimelineError) as exc:
        raise RuntimeError(encode_generation_problem(render_problem(exc))) from exc


__all__ = [
    "RENDER_FINAL_CUT_TASK_TYPE",
    "RENDER_JIANYING_DRAFT_TASK_TYPE",
    "RENDER_TASK_EXECUTORS",
    "RenderTaskExecutor",
    "RenderTaskRequest",
    "execute_final_cut_task",
    "execute_jianying_draft_task",
    "execute_render_task",
    "final_cut_task_request",
    "final_cut_task_result",
    "jianying_draft_task_request",
    "render_problem",
]
