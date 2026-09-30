"""一集草稿的 Web 端点：列出、读取、手修保存（违约清零即采用）与丢弃。

四种草稿（参考生视频、剧情演绎、旁白/解说的脚本规划草稿，参考生视频的提示词编写草稿）共用这组端点，
``doc_type`` 取值同 Agent 草稿工具。路由只转发给 ``EpisodeDraftService``。
"""

import logging
from typing import Any, NoReturn

from fastapi import APIRouter, Body
from pydantic import BaseModel, ConfigDict, Field

from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError, UnprocessableError
from lib.project.project_manager import get_project_manager
from server.draft_workflow import DraftWorkflowError
from server.i18n import Translator
from server.routers.script_review import localize_draft_view
from server.services.project.episode_drafts import EpisodeDraftService

logger = logging.getLogger(__name__)

router = APIRouter()

_CONFLICT_KEYS: dict[str, str] = {
    "revision_conflict": "draft_revision_conflict",
    "formal_revision_conflict": "draft_formal_revision_conflict",
    "draft_agent_owned": "draft_agent_owned",
    "script_plan_confirmed": "script_review_script_plan_confirmed",
}


def _raise_draft_error(exc: DraftWorkflowError, episode: int) -> NoReturn:
    """把草稿命令的错误码映射为 HTTP 响应；Agent 面向的 ``detail`` 只作诊断附带。"""
    if exc.code == "draft_not_found":
        raise NotFoundError("draft_not_found", episode=episode) from exc
    if exc.code in _CONFLICT_KEYS:
        raise ConflictError(_CONFLICT_KEYS[exc.code]).with_diagnostic({"code": exc.code}) from exc
    if exc.code in {"invalid_request", "doc_type_not_applicable"}:
        raise BadRequestError("draft_doc_type_not_applicable").with_diagnostic({"code": exc.code}) from exc
    # detail 面向 Agent，可能带服务端路径：只进日志，响应只带错误码。
    logger.warning("草稿命令失败 episode=%s code=%s：%s", episode, exc.code, exc.detail)
    raise UnprocessableError("draft_save_failed").with_diagnostic({"code": exc.code}) from exc


class SaveDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: dict[str, Any] = Field(description="修改后的完整草稿正文")
    base_revision: str = Field(description="读取草稿时拿到的 revision")


@router.get("/projects/{project_name}/episodes/{episode}/drafts")
async def list_episode_drafts(project_name: str, episode: int):
    """本集在场的草稿摘要。"""
    try:
        drafts = await EpisodeDraftService(get_project_manager()).list_drafts(project_name, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    return {"episode": episode, "drafts": drafts}


@router.get("/projects/{project_name}/episodes/{episode}/drafts/{doc_type}")
async def get_episode_draft(project_name: str, episode: int, doc_type: str, _t: Translator):
    """一份草稿的呈现视图：违约与降级提示逐条目定位；Agent 的可编辑草稿不带正文。"""
    try:
        view = await EpisodeDraftService(get_project_manager()).get_draft(project_name, episode, doc_type)
    except DraftWorkflowError as exc:
        _raise_draft_error(exc, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    return localize_draft_view(view, _t)


@router.put("/projects/{project_name}/episodes/{episode}/drafts/{doc_type}")
async def save_episode_draft(
    project_name: str,
    episode: int,
    doc_type: str,
    _t: Translator,
    req: SaveDraftRequest = Body(...),
):
    """手修保存：服务端全量重判，违约清零即采用为正式内容，否则返回刷新后的草稿视图。"""
    try:
        result = await EpisodeDraftService(get_project_manager()).save_draft(
            project_name, episode, doc_type, req.content, req.base_revision
        )
    except DraftWorkflowError as exc:
        _raise_draft_error(exc, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    result["draft"] = localize_draft_view(result["draft"], _t)
    return result


@router.delete("/projects/{project_name}/episodes/{episode}/drafts/{doc_type}")
async def discard_episode_draft(project_name: str, episode: int, doc_type: str, base_revision: str = ""):
    """丢弃草稿，回到正式内容。"""
    try:
        return await EpisodeDraftService(get_project_manager()).discard_draft(
            project_name, episode, doc_type, base_revision
        )
    except DraftWorkflowError as exc:
        _raise_draft_error(exc, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
