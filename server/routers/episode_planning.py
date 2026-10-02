"""AI 分集规划的 Web 入口：AI 规划分集与重新规划。

「AI 规划分集」调用 :func:`server.tool_runtime.start_episode_planning`：准入（有整本源文、没有悬而未决的
重新规划候选）与 Agent 的 ``plan_episodes`` 同一个谓词，从账本推导的规划起点逐窗规划到整本源文结尾，每一窗是
一个排队的文本任务。提交后立即返回首窗的生成批次，进度经任务事件与项目快照呈现。附加指令随任务传递，不写进项目。

「从这一集开始重新规划」调用 :func:`server.tool_runtime.start_episode_replan`：逐窗生成一份候选，分集账本不动；
生成中途停止后可以接着生成（:func:`server.tool_runtime.continue_episode_replan`）。候选的采纳与放弃见
:mod:`lib.episode.episode_replan`。
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from lib.episode.episode_replan import (
    ReplanAdoptionResult,
    ReplanConfirmationRequired,
    ReplanError,
    ReplanScope,
    adopt_replan_candidate,
    discard_replan_candidate,
    render_replan_adoption_text,
)
from lib.infra.api_errors import ApiError, ConflictError, NotFoundError, UnprocessableError
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import ProjectManager, get_project_manager
from server.agent_toolset.envelope import json_value
from server.auth import CurrentUser
from server.dependencies import require_project_migration_ok
from server.i18n import Translator
from server.routers.episode_management import episode_has_active_tasks
from server.text_generation import MAX_INSTRUCTIONS_LEN
from server.tool_runtime import (
    CallerContext,
    PlanEpisodesRequest,
    ProjectScope,
    Services,
    ToolProblem,
    ToolRequest,
    continue_episode_replan,
    episode_planning_active,
    start_episode_planning,
    start_episode_replan,
    stop_episode_planning,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class PlanningGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file: str
    end: Annotated[int, Field(gt=0)]


class EpisodePlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: Annotated[str, Field(max_length=MAX_INSTRUCTIONS_LEN)] | None = Field(
        default=None, description="附加指令原文；空白视同未传"
    )
    gap: PlanningGap | None = Field(
        default=None, description="只规划以这一点为终点的那段未切分原文；缺省时从规划起点规划到整本源文结尾"
    )


#: 重新规划被拒的原因码 → 状态码；未列出的为 409。
_REPLAN_STATUS: dict[str, int] = {
    "episode_not_found": 404,
    "candidate_not_found": 404,
    "not_cut_episode": 422,
    "whole_source_missing": 422,
    "episode_not_placed": 422,
    "ledger_invalid": 422,
    "candidate_invalid": 422,
    "candidate_empty": 422,
}


def _replan_http_error(code: str, _t: Translator) -> HTTPException:
    return HTTPException(status_code=_REPLAN_STATUS.get(code, 409), detail=_t(f"episode_replan_{code}"))


def _raise_problem(problem: ToolProblem, _t: Translator) -> None:
    params = problem.params or {}
    if problem.code == "generation_active_task_conflict":
        raise ConflictError("episode_planning_task_active").with_diagnostic(params)
    if problem.code == "episode_replan_refused" and isinstance(params.get("reason"), str):
        raise _replan_http_error(params["reason"], _t)
    reason = params.get("reason")
    if problem.code == "operation_not_admitted" and isinstance(reason, str):
        text = _t(f"operation_{reason}", where=_t("operation_project"))
    else:
        text = problem.detail
    raise UnprocessableError("episode_planning_refused", reason=text).with_diagnostic(problem.detail)


def _context(project_name: str, user_id: str) -> tuple[ProjectScope, CallerContext, Services]:
    pm = get_project_manager()
    return (
        ProjectScope(project_name=project_name, data_root=pm.data_root),
        CallerContext(user_id=user_id, source="webui"),
        Services.defaults(pm),
    )


@router.post("/projects/{project_name}/episode-planning")
async def plan_episodes_to_end(project_name: str, req: EpisodePlanningRequest, user: CurrentUser, _t: Translator):
    """提交 AI 分集规划，从规划起点逐窗规划到整本源文结尾，返回首窗的生成批次。

    带 ``gap`` 时是「规划这段未切分的原文」：只规划到这段原文的结尾，新集按源文位置插入。
    """
    scope, caller, services = _context(project_name, user.id)
    outcome = await start_episode_planning(
        ToolRequest(PlanEpisodesRequest(instructions=req.instructions)),
        scope,
        caller,
        services,
        gap=None if req.gap is None else (req.gap.source_file, req.gap.end),
    )
    if outcome.problem is not None:
        _raise_problem(outcome.problem, _t)
    return {"batch": json_value(outcome.value)}


@router.post("/projects/{project_name}/episode-planning/stop")
async def stop_planning(project_name: str, user: CurrentUser):
    """停止分集规划：取消排队中的窗口；执行中的那一窗照常完成，已切出的集保留。"""
    scope, caller, services = _context(project_name, user.id)
    result = await stop_episode_planning(scope, caller, services)
    return {"cancelled": result.cancelled, "running": result.running}


class EpisodeReplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 从哪一集开始重新规划（集 ID）。
    episode: Annotated[int, Field(gt=0)]
    instructions: Annotated[str, Field(max_length=MAX_INSTRUCTIONS_LEN)] | None = Field(
        default=None, description="附加指令原文；空白视同未传"
    )
    dry_run: bool = Field(default=False, description="只返回重新规划的范围，不发起")


@router.post("/projects/{project_name}/episode-replan", dependencies=[Depends(require_project_migration_ok)])
async def start_replan(project_name: str, req: EpisodeReplanRequest, user: CurrentUser, _t: Translator):
    """从某一集开始重新规划：逐窗生成一份候选，分集账本不动，返回首窗的生成批次。

    ``dry_run`` 时返回 ``status=preview``：会被替换的切出集 ``replaced`` 与其中已开始制作的集 ``started``。
    """
    scope, caller, services = _context(project_name, user.id)
    outcome = await start_episode_replan(
        ToolRequest(PlanEpisodesRequest(instructions=req.instructions)),
        scope,
        caller,
        services,
        episode=req.episode,
        dry_run=req.dry_run,
    )
    if outcome.problem is not None:
        _raise_problem(outcome.problem, _t)
    if isinstance(outcome.value, ReplanScope):
        return {"status": "preview", **asdict(outcome.value)}
    return {"batch": json_value(outcome.value)}


class ReplanContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str


@router.post("/projects/{project_name}/episode-replan/continue", dependencies=[Depends(require_project_migration_ok)])
async def continue_replan(project_name: str, req: ReplanContinueRequest, user: CurrentUser, _t: Translator):
    """接着生成中途停止的新的分集方案：从方案的结尾逐窗生成到整本源文结尾，沿用发起时的附加指令。

    返回首窗的生成批次。方案已覆盖到结尾、已过时或已不在时按原因码拒绝；分集规划在进行时 409。
    """
    scope, caller, services = _context(project_name, user.id)
    outcome = await continue_episode_replan(req.candidate_id, scope, caller, services)
    if outcome.problem is not None:
        _raise_problem(outcome.problem, _t)
    return {"batch": json_value(outcome.value)}


class ReplanAdoptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    #: 创作者看过的变更清单的 ``revision``；缺省或与当前清单不符时只返回清单，不采纳。
    revision: str | None = None
    #: 一并删除退下的集（已开始制作、转为无原文的集）。
    delete_retired: bool = False


class ReplanDiscardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str


async def _run_replan[T](name: str, user_id: str, _t: Translator, action: Callable[[ProjectManager], T]) -> T:
    """候选生成进行中时拒绝；其余在线程里执行，``ReplanError`` 映射为对应状态码与文案。"""
    scope, caller, services = _context(name, user_id)
    if not services.projects.project_exists(name):
        raise NotFoundError("project_not_found", name=name)
    if await episode_planning_active(scope, caller, services):
        raise HTTPException(status_code=409, detail=_t("episode_replan_generating"))

    def _sync() -> T:
        with project_change_source("webui"):
            return action(services.projects)

    try:
        return await asyncio.to_thread(_sync)
    except ReplanError as exc:
        raise _replan_http_error(exc.code, _t) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post("/projects/{project_name}/episode-replan/adopt", dependencies=[Depends(require_project_migration_ok)])
async def adopt_replan(project_name: str, req: ReplanAdoptRequest, user: CurrentUser, _t: Translator) -> dict[str, Any]:
    """采纳新的分集方案。

    先不带 ``revision`` 调用，得到 ``status=confirmation_required`` 与服务端成文的 ``impact.text``（采纳的后果）和
    ``impact.delete_text``（勾选「一并删除」时会丢失的内容）；确认后带上 ``impact.revision`` 重新提交。清单在两次调用
    之间变了时再次返回确认，不采纳。
    """
    if req.revision is not None:
        # 与手工切分同一道检查：要退下或移除的集有在途任务时拒绝，免得任务把产物写进被替换的集
        preview = await _run_replan(
            project_name,
            user.id,
            _t,
            lambda manager: adopt_replan_candidate(manager.get_project_path(project_name), req.candidate_id),
        )
        if isinstance(preview, ReplanConfirmationRequired):
            for episode in (*preview.impact.retired, *preview.impact.removed):
                if await episode_has_active_tasks(project_name, episode):
                    raise HTTPException(status_code=409, detail=_t("episode_replan_tasks_active"))

    def _adopt(manager: ProjectManager) -> dict[str, Any]:
        outcome = adopt_replan_candidate(
            manager.get_project_path(project_name),
            req.candidate_id,
            revision=req.revision,
            delete_retired=req.delete_retired,
        )
        if isinstance(outcome, ReplanAdoptionResult):
            return {"status": "adopted", "episodes": outcome.episodes, "deleted": outcome.deleted}
        impact = outcome.impact.to_dict()
        texts = render_replan_adoption_text(impact, manager.load_project(project_name), _t)
        return {"status": "confirmation_required", "impact": {**impact, **texts}}

    return await _run_replan(project_name, user.id, _t, _adopt)


@router.post("/projects/{project_name}/episode-replan/discard", dependencies=[Depends(require_project_migration_ok)])
async def discard_replan(project_name: str, req: ReplanDiscardRequest, user: CurrentUser, _t: Translator):
    """放弃新的分集方案：分集账本什么都不变。"""

    def _discard(manager: ProjectManager) -> dict[str, Any]:
        discard_replan_candidate(manager.get_project_path(project_name), req.candidate_id)
        return {"status": "discarded"}

    return await _run_replan(project_name, user.id, _t, _discard)
