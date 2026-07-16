"""step1→step2 web 审核 gate 路由。

暴露结构化中间态的审阅 / 编辑 / 确认：step1 产出后中间态在 web 可见可改，用户显式确认后才放行
step2 视觉生成（step2 由 agent 的 generate_episode_script 执行，读时经 gate 校验阻塞到确认）。
drama（utterances + source_text）与 narration（结构化 novel_text）共用本机制。
"""

import asyncio
import logging

from fastapi import APIRouter, Body, HTTPException

from lib.i18n import Translator
from lib.project_manager import get_project_manager
from server.auth import CurrentUser
from server.services.script_review import ScriptReviewError, ScriptReviewService

logger = logging.getLogger(__name__)

router = APIRouter()

# gate 领域错误码 → (HTTP 状态, i18n key)。invalid_content / episode_not_found 带参数另行注入。
_ERROR_STATUS: dict[str, int] = {
    "not_applicable": 409,
    "no_step1": 409,
    "invalid_content": 422,
    "episode_not_found": 404,
}
# 仅无参错误码走本映射；invalid_content / episode_not_found 需注参，在 _raise_review_error 单独处理。
_ERROR_I18N: dict[str, str] = {
    "not_applicable": "script_review_not_applicable",
    "no_step1": "script_review_no_step1",
}


def _raise_review_error(exc: ScriptReviewError, episode: int, _t: Translator) -> None:
    status = _ERROR_STATUS.get(exc.code, 400)
    if exc.code == "invalid_content":
        detail = _t("script_review_invalid_content", details=exc.message)
    elif exc.code == "episode_not_found":
        detail = _t("episode_not_found", episode=episode)
    else:
        detail = _t(_ERROR_I18N.get(exc.code, "internal_server_error"))
    raise HTTPException(status_code=status, detail=detail)


@router.get("/projects/{project_name}/episodes/{episode}/script-review")
async def get_script_review(project_name: str, episode: int, _user: CurrentUser, _t: Translator):
    """读取该集 step1 结构化中间态 + 审核状态（供 web 渲染与编辑）。"""
    try:
        service = ScriptReviewService(get_project_manager())
        return await asyncio.to_thread(service.get_state, project_name, episode)
    except ScriptReviewError as exc:
        _raise_review_error(exc, episode, _t)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))


@router.put("/projects/{project_name}/episodes/{episode}/script-review/content")
async def update_script_review_content(
    project_name: str,
    episode: int,
    _user: CurrentUser,
    _t: Translator,
    content: dict = Body(...),
):
    """保存手动 / agent 编辑后的结构化中间态，并使该集重新进入待审。"""
    try:
        service = ScriptReviewService(get_project_manager())
        return await asyncio.to_thread(service.save_content, project_name, episode, content)
    except ScriptReviewError as exc:
        _raise_review_error(exc, episode, _t)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))


@router.post("/projects/{project_name}/episodes/{episode}/script-review/confirm")
async def confirm_script_review(project_name: str, episode: int, _user: CurrentUser, _t: Translator):
    """用户显式确认 step1 内容，放行 step2 视觉生成。"""
    try:
        service = ScriptReviewService(get_project_manager())
        return await asyncio.to_thread(service.confirm, project_name, episode)
    except ScriptReviewError as exc:
        _raise_review_error(exc, episode, _t)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
