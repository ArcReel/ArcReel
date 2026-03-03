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
from server.agent_runtime.service import AssistantService

router = APIRouter()

assistant_service = AssistantService(project_root=PROJECT_ROOT)


def get_assistant_service() -> AssistantService:
    return assistant_service


def _validate_session_ownership(
    service: AssistantService, session_id: str, project_name: str
) -> None:
    """Validate session belongs to the specified project."""
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    if session.project_name != project_name:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")

def _assistant_service_for_stream(
    project_name: str,
    session_id: str,
) -> AssistantService:
    service = get_assistant_service()
    _validate_session_ownership(service, session_id, project_name)
    return service


class CreateSessionRequest(BaseModel):
    title: Optional[str] = ""


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)


class AnswerQuestionRequest(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)


class UpdateSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.post("/sessions")
async def create_session(project_name: str, req: CreateSessionRequest):
    try:
        service = get_assistant_service()
        session = await service.create_session(project_name, req.title or "")
        return {"id": session.id, "status": session.status, "created_at": session.created_at}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions")
async def list_sessions(
    project_name: str,
    status: Optional[Literal["idle", "running", "completed", "error", "interrupted"]] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    try:
        sessions = get_assistant_service().list_sessions(
            project_name=project_name, status=status, limit=limit, offset=offset
        )
        return {"sessions": [s.model_dump() for s in sessions]}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{session_id}")
async def get_session(project_name: str, session_id: str):
    try:
        service = get_assistant_service()
        _validate_session_ownership(service, session_id, project_name)
        session = service.get_session(session_id)
        return session.model_dump()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/sessions/{session_id}")
async def update_session(project_name: str, session_id: str, req: UpdateSessionRequest):
    try:
        service = get_assistant_service()
        _validate_session_ownership(service, session_id, project_name)
        session = service.update_session_title(session_id, req.title)
        if session is None:
            raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
        return {"success": True, "session": session.model_dump()}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/sessions/{session_id}")
async def delete_session(project_name: str, session_id: str):
    try:
        service = get_assistant_service()
        _validate_session_ownership(service, session_id, project_name)
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
async def list_messages(project_name: str, session_id: str):
    raise HTTPException(
        status_code=410,
        detail="messages 接口已下线，请使用 /snapshot 与 SSE stream 协议。",
    )


@router.get("/sessions/{session_id}/snapshot")
async def get_snapshot(project_name: str, session_id: str):
    try:
        service = get_assistant_service()
        _validate_session_ownership(service, session_id, project_name)
        snapshot = await service.get_snapshot(session_id)
        return snapshot
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions/{session_id}/messages")
async def send_message(project_name: str, session_id: str, req: SendMessageRequest):
    try:
        service = get_assistant_service()
        _validate_session_ownership(service, session_id, project_name)
        result = await service.send_message(session_id, req.content)
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


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(project_name: str, session_id: str):
    try:
        service = get_assistant_service()
        _validate_session_ownership(service, session_id, project_name)
        result = await service.interrupt_session(session_id)
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
async def answer_question(project_name: str, session_id: str, question_id: str, req: AnswerQuestionRequest):
    if not req.answers:
        raise HTTPException(status_code=400, detail="answers 不能为空")
    try:
        service = get_assistant_service()
        _validate_session_ownership(service, session_id, project_name)
        result = await service.answer_user_question(
            session_id=session_id,
            question_id=question_id,
            answers=req.answers,
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
    service: AssistantService = Depends(_assistant_service_for_stream),
) -> AsyncIterator[ServerSentEvent]:
    try:
        async for event in service.stream_events(session_id):
            yield event
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/skills")
async def list_skills(project_name: str):
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
