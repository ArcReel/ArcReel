"""
Assistant session APIs.
"""

import logging
from collections.abc import AsyncIterator
from typing import Literal, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from lib import PROJECT_ROOT
from server.agent_runtime.models import SessionMeta
from server.agent_runtime.service import AssistantService
from server.agent_runtime.session_manager import SessionCapacityError
from server.auth import CurrentUser, CurrentUserFlexible

router = APIRouter()

assistant_service = AssistantService(project_root=PROJECT_ROOT)


def get_assistant_service() -> AssistantService:
    return assistant_service


async def _validate_session_ownership(
    service: AssistantService, session_id: str, project_name: str
) -> "SessionMeta":
    """Validate session belongs to the specified project and return it."""
    session = await service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    if session.project_name != project_name:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    return session

async def _assistant_service_for_stream(
    project_name: str,
    session_id: str,
) -> tuple[AssistantService, SessionMeta]:
    service = get_assistant_service()
    meta = await _validate_session_ownership(service, session_id, project_name)
    return service, meta


class ImageAttachment(BaseModel):
    data: str
    media_type: str


class SendRequest(BaseModel):
    content: str = ""
    images: list[ImageAttachment] = Field(default_factory=list, max_length=5)
    session_id: Optional[str] = None


class AnswerQuestionRequest(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)


@router.post("/sessions/send")
async def send_message(
    project_name: str,
    req: SendRequest,
    _user: CurrentUser,
):
    try:
        service = get_assistant_service()
        result = await service.send_or_create(
            project_name,
            req.content,
            session_id=req.session_id,
            images=req.images,
        )
        return result
    except SessionCapacityError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="会话或项目不存在")
    except TimeoutError:
        raise HTTPException(status_code=504, detail="SDK 会话创建超时")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions")
async def list_sessions(
    project_name: str,
    _user: CurrentUser,
    status: Optional[Literal["idle", "running", "completed", "error", "interrupted"]] = None,
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
async def get_session(project_name: str, session_id: str, _user: CurrentUser):
    try:
        service = get_assistant_service()
        session = await _validate_session_ownership(service, session_id, project_name)
        return session.model_dump()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/sessions/{session_id}")
async def delete_session(project_name: str, session_id: str, _user: CurrentUser):
    try:
        service = get_assistant_service()
        await _validate_session_ownership(service, session_id, project_name)
        deleted = await service.delete_session(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{session_id}/messages")
async def list_messages(project_name: str, session_id: str, _user: CurrentUser):
    raise HTTPException(
        status_code=410,
        detail="messages 接口已下线，请使用 /snapshot 与 SSE stream 协议。",
    )


@router.get("/sessions/{session_id}/snapshot")
async def get_snapshot(project_name: str, session_id: str, _user: CurrentUser):
    try:
        service = get_assistant_service()
        meta = await _validate_session_ownership(service, session_id, project_name)
        snapshot = await service.get_snapshot(session_id, meta=meta)
        return snapshot
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(project_name: str, session_id: str, _user: CurrentUser):
    try:
        service = get_assistant_service()
        meta = await _validate_session_ownership(service, session_id, project_name)
        result = await service.interrupt_session(session_id, meta=meta)
        return result
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions/{session_id}/questions/{question_id}/answer")
async def answer_question(project_name: str, session_id: str, question_id: str, req: AnswerQuestionRequest, _user: CurrentUser):
    if not req.answers:
        raise HTTPException(status_code=400, detail="answers 不能为空")
    try:
        service = get_assistant_service()
        meta = await _validate_session_ownership(service, session_id, project_name)
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
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
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
async def list_skills(project_name: str, _user: CurrentUser):
    try:
        skills = get_assistant_service().list_available_skills(project_name=project_name)
        return {"skills": skills}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))
