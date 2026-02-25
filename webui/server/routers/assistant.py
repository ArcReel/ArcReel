"""
Assistant session APIs.
"""

import logging
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from webui.server.agent_runtime.service import AssistantService

router = APIRouter()

project_root = Path(__file__).parent.parent.parent.parent
assistant_service = AssistantService(project_root=project_root)


class CreateSessionRequest(BaseModel):
    project_name: str = Field(min_length=1)
    title: Optional[str] = ""


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)


class AnswerQuestionRequest(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)


class UpdateSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.post("/sessions")
async def create_session(req: CreateSessionRequest):
    try:
        session = await assistant_service.create_session(req.project_name, req.title or "")
        return {"id": session.id, "status": session.status, "created_at": session.created_at}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{req.project_name}' 不存在")
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions")
async def list_sessions(
    project_name: Optional[str] = None,
    status: Optional[Literal["idle", "running", "completed", "error", "interrupted"]] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    try:
        sessions = assistant_service.list_sessions(
            project_name=project_name, status=status, limit=limit, offset=offset
        )
        return {"sessions": [s.model_dump() for s in sessions]}
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    try:
        session = assistant_service.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
        return session.model_dump()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/sessions/{session_id}")
async def update_session(session_id: str, req: UpdateSessionRequest):
    try:
        session = assistant_service.update_session_title(session_id, req.title)
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
async def delete_session(session_id: str):
    try:
        deleted = await assistant_service.delete_session(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{session_id}/messages")
async def list_messages(session_id: str):
    raise HTTPException(
        status_code=410,
        detail="messages 接口已下线，请使用 /snapshot 与 SSE stream 协议。",
    )


@router.get("/sessions/{session_id}/snapshot")
async def get_snapshot(session_id: str):
    try:
        snapshot = await assistant_service.get_snapshot(session_id)
        return snapshot
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, req: SendMessageRequest):
    try:
        result = await assistant_service.send_message(session_id, req.content)
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(session_id: str):
    try:
        result = await assistant_service.interrupt_session(session_id)
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions/{session_id}/questions/{question_id}/answer")
async def answer_question(session_id: str, question_id: str, req: AnswerQuestionRequest):
    if not req.answers:
        raise HTTPException(status_code=400, detail="answers 不能为空")
    try:
        result = await assistant_service.answer_user_question(
            session_id=session_id,
            question_id=question_id,
            answers=req.answers,
        )
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{session_id}/stream")
async def stream_events(session_id: str):
    try:
        session = assistant_service.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")

        return StreamingResponse(
            assistant_service.stream_events(session_id),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/skills")
async def list_skills(project_name: Optional[str] = None):
    try:
        skills = assistant_service.list_available_skills(project_name=project_name)
        return {"skills": skills}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))
