"""把一个本地渲染任务作为单成员生成批次提交到 ``render`` 车道。

ArcReel Agent 等到任务终态拿到任务结果；外部 Agent 立即拿到批次句柄，用 ``get_generation_batch`` 轮询。
成片与剪映草稿的工具共用这一入口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lib.generation.generation_batch import (
    GenerationBatchReadModel,
    GenerationBatchRequestedItem,
    GenerationBatchRequestSnapshot,
)
from lib.generation.generation_queue import ActiveTaskRequestConflict, cleanup_fresh_generation_batch
from lib.generation.generation_queue_client import WorkerOfflineError, enqueue_task_only, wait_for_task
from lib.generation.generation_result import (
    GenerationAction,
    GenerationSelectionMode,
    enqueue_problem,
    problem_from_task_failure,
)
from server.services.tasks.render_tasks import RenderTaskRequest
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolProblem


@dataclass(frozen=True, slots=True)
class RenderSubmission:
    """提交结果：外部调用方只有 ``batch``；内嵌调用方另有成功任务的结果，失败时为 ``problem``。"""

    batch: GenerationBatchReadModel
    result: dict[str, Any] | None = None
    problem: ToolProblem | None = None


def _problem(problem: Any) -> ToolProblem:
    return ToolProblem(**problem.model_dump(mode="json"))


async def submit_render_task(
    request: RenderTaskRequest,
    *,
    operation: str,
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> RenderSubmission | ToolProblem:
    snapshot = GenerationBatchRequestSnapshot(
        selection=GenerationSelectionMode.EXPLICIT,
        requested=[
            GenerationBatchRequestedItem(
                unit_id=request.resource_id,
                artifact_key=request.artifact_key,
                artifact_path=request.artifact_path,
            )
        ],
    )
    batch_id = await services.queue.create_generation_batch(
        project_name=scope.project_name,
        operation=operation,
        requested=snapshot,
        blocked=[],
        source=caller.source,
        user_id=caller.user_id,
    )
    try:
        enqueued = await enqueue_task_only(
            project_name=scope.project_name,
            **request.enqueue_fields(),
            source=caller.source,
            user_id=caller.user_id,
            batch_id=batch_id,
            batch_unit_id=request.resource_id,
            queue=services.queue,
        )
        if caller.source == "mcp":
            batch = await services.queue.get_generation_batch(
                project_name=scope.project_name, batch_id=batch_id, user_id=caller.user_id
            )
            return RenderSubmission(batch=batch)
        task = await wait_for_task(enqueued["task_id"], queue=services.queue)
        batch = await services.queue.get_generation_batch(
            project_name=scope.project_name, batch_id=batch_id, user_id=caller.user_id
        )
    except WorkerOfflineError as exc:
        await cleanup_fresh_generation_batch(
            services.queue, project_name=scope.project_name, batch_id=batch_id, user_id=caller.user_id, failure=exc
        )
        return _problem(enqueue_problem(str(exc)))
    except ActiveTaskRequestConflict as exc:
        await cleanup_fresh_generation_batch(
            services.queue, project_name=scope.project_name, batch_id=batch_id, user_id=caller.user_id, failure=exc
        )
        return ToolProblem(
            "generation_active_task_conflict",
            "同一产物已有一个参数不同的渲染任务在排队或执行，请等它结束后再提交",
            action=GenerationAction.WAIT_FOR_TASK,
            params={"task_id": exc.existing_task_id},
        )
    except BaseException as exc:
        await cleanup_fresh_generation_batch(
            services.queue, project_name=scope.project_name, batch_id=batch_id, user_id=caller.user_id, failure=exc
        )
        raise
    if task["status"] in {"failed", "cancelled"}:
        problem = problem_from_task_failure(task.get("error_message"), cancelled=task["status"] == "cancelled")
        return RenderSubmission(batch=batch, problem=_problem(problem))
    return RenderSubmission(batch=batch, result=task.get("result") or {})


__all__ = ["RenderSubmission", "submit_render_task"]
