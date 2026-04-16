"""
Assistant session APIs.
"""

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from lib import PROJECT_ROOT
from lib.i18n import Translator, get_locale
from server.agent_runtime.models import SessionMeta
from server.agent_runtime.service import AssistantService
from server.agent_runtime.session_manager import SessionCapacityError
from server.auth import CurrentUser, CurrentUserFlexible
from server.rate_limiter import RATE_LIMIT_AGENT, limiter

router = APIRouter()

assistant_service = AssistantService(project_root=PROJECT_ROOT)


def get_assistant_service() -> AssistantService:
    return assistant_service


async def _validate_session_ownership(
    service: AssistantService, session_id: str, project_name: str, _t: Callable[..., str]
) -> "SessionMeta":
    """Validate session belongs to the specified project and return it."""
    session = await service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=_t("session_not_found", session_id=session_id))
    if session.project_name != project_name:
        raise HTTPException(status_code=404, detail=_t("session_not_found", session_id=session_id))
    return session


async def _assistant_service_for_stream(
    project_name: str,
    session_id: str,
    _t: Translator,
) -> tuple[AssistantService, SessionMeta]:
    service = get_assistant_service()
    meta = await _validate_session_ownership(service, session_id, project_name, _t)
    return service, meta


class ImageAttachment(BaseModel):
    data: str
    media_type: str


class SendRequest(BaseModel):
    content: str = ""
    images: list[ImageAttachment] = Field(default_factory=list, max_length=5)
    session_id: str | None = None


class AnswerQuestionRequest(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["allow", "deny"]
    updated_input: dict[str, Any] | None = None
    message: str | None = None


@router.post("/sessions/send")
@limiter.limit(RATE_LIMIT_AGENT)
async def send_message(
    project_name: str,
    req: SendRequest,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
):
    try:
        service = get_assistant_service()
        result = await service.send_or_create(
            project_name,
            req.content,
            session_id=req.session_id,
            images=req.images,
            locale=get_locale(request),
        )
        return result
    except SessionCapacityError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("session_or_project_not_found"))
    except TimeoutError:
        raise HTTPException(status_code=504, detail=_t("sdk_session_timeout"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions")
async def list_sessions(
    project_name: str,
    _user: CurrentUser,
    status: Literal["idle", "running", "completed", "error", "interrupted"] | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    try:
        sessions = await get_assistant_service().list_sessions(
            project_name=project_name, status=status, limit=limit, offset=offset
        )
        return {"sessions": [s.model_dump() for s in sessions]}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{session_id}")
async def get_session(project_name: str, session_id: str, _user: CurrentUser, _t: Translator):
    try:
        service = get_assistant_service()
        session = await _validate_session_ownership(service, session_id, project_name, _t)
        return session.model_dump()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/sessions/{session_id}")
async def delete_session(project_name: str, session_id: str, _user: CurrentUser, _t: Translator):
    try:
        service = get_assistant_service()
        await _validate_session_ownership(service, session_id, project_name, _t)
        deleted = await service.delete_session(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=_t("session_not_found", session_id=session_id))
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{session_id}/messages")
async def list_messages(project_name: str, session_id: str, _user: CurrentUser, _t: Translator):
    raise HTTPException(
        status_code=410,
        detail=_t("interface_offline"),
    )


@router.get("/sessions/{session_id}/snapshot")
async def get_snapshot(project_name: str, session_id: str, _user: CurrentUser, _t: Translator):
    try:
        service = get_assistant_service()
        meta = await _validate_session_ownership(service, session_id, project_name, _t)
        snapshot = await service.get_snapshot(session_id, meta=meta)
        return snapshot
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("session_not_found", session_id=session_id))
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions/{session_id}/interrupt")
@limiter.limit(RATE_LIMIT_AGENT)
async def interrupt_session(project_name: str, session_id: str, request: Request, _user: CurrentUser, _t: Translator):
    try:
        service = get_assistant_service()
        meta = await _validate_session_ownership(service, session_id, project_name, _t)
        result = await service.interrupt_session(session_id, meta=meta)
        return result
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("session_not_found", session_id=session_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions/{session_id}/questions/{question_id}/answer")
@limiter.limit(RATE_LIMIT_AGENT)
async def answer_question(
    project_name: str,
    session_id: str,
    question_id: str,
    req: AnswerQuestionRequest,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
):
    if not req.answers:
        raise HTTPException(status_code=400, detail=_t("answers_required"))
    try:
        service = get_assistant_service()
        meta = await _validate_session_ownership(service, session_id, project_name, _t)
        result = await service.answer_user_question(
            session_id=session_id,
            question_id=question_id,
            answers=req.answers,
            meta=meta,
        )
        return result
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("session_not_found", session_id=session_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions/{session_id}/approvals/{request_id}/decision")
@limiter.limit(RATE_LIMIT_AGENT)
async def decide_tool_approval(
    project_name: str,
    session_id: str,
    request_id: str,
    req: ApprovalDecisionRequest,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
):
    try:
        service = get_assistant_service()
        meta = await _validate_session_ownership(service, session_id, project_name, _t)
        result = await service.answer_tool_approval(
            session_id=session_id,
            request_id=request_id,
            decision=req.decision,
            updated_input=req.updated_input,
            message=req.message,
            meta=meta,
        )
        return result
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("session_not_found", session_id=session_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))



@router.post("/sessions/{session_id}/undo-last-write")
@limiter.limit(RATE_LIMIT_AGENT)
async def undo_last_write(
    project_name: str,
    session_id: str,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
):
    """Restore the file last written/edited in this session.

    Returns 409 when no undo backup is available (write never approved, session
    evicted, or backup already consumed by a previous undo call).
    """
    try:
        service = get_assistant_service()
        await _validate_session_ownership(service, session_id, project_name, _t)
        result = await service.undo_last_write(session_id)
        return result
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("session_not_found", session_id=session_id))
    except ValueError:
        raise HTTPException(status_code=409, detail="no_undo_available")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"file_restore_failed: {exc}")
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{session_id}/stream", response_class=EventSourceResponse)
async def stream_events(
    project_name: str,
    session_id: str,
    _user: CurrentUserFlexible,
    deps: tuple[AssistantService, SessionMeta] = Depends(_assistant_service_for_stream),
) -> AsyncIterator[ServerSentEvent]:
    service, meta = deps
    try:
        async for event in service.stream_events(session_id, meta=meta):
            yield event
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/skills")
async def list_skills(project_name: str, _user: CurrentUser, _t: Translator):
    try:
        skills = get_assistant_service().list_available_skills(project_name=project_name)
        return {"skills": skills}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))
