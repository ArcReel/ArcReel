"""
Assistant session APIs.
"""

import logging
from collections.abc import AsyncIterator, Callable
from typing import Literal

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from lib import PROJECT_ROOT
from lib.api_errors import BadRequestError, ConflictError, NotFoundError, ServiceUnavailableError
from lib.i18n import Translator, get_locale
from server.agent_runtime.failure_observation import build_startup_failure_observation
from server.agent_runtime.models import SessionMeta
from server.agent_runtime.service import (
    AssistantService,
    InterruptSettleTimeoutError,
    PendingQuestionError,
    RewriteAnchorError,
    RewriteUnavailableError,
    SessionSupersededError,
)
from server.agent_runtime.session_branch import SessionBranchError
from server.agent_runtime.session_manager import AgentStartupError, SessionBusyError, SessionCapacityError
from server.auth import CurrentUserFlexible

router = APIRouter()

# 自带认证端点：EventSource 是浏览器直发请求，带不了 Authorization header，
# 端点内声明 CurrentUserFlexible 收 ?token=，注册时不挂 Bearer 依赖。
self_auth_router = APIRouter()

assistant_service = AssistantService(project_root=PROJECT_ROOT)

MAX_IMAGE_SOURCE_BYTES = 5 * 1024 * 1024
# Base64 needs four characters for every three source bytes, rounded up to a full quartet.
MAX_IMAGE_BASE64_CHARS = 4 * ((MAX_IMAGE_SOURCE_BYTES + 2) // 3)
MAX_IMAGES_PER_REQUEST = 5
MAX_IMAGES_TOTAL_BASE64_CHARS = MAX_IMAGES_PER_REQUEST * MAX_IMAGE_BASE64_CHARS


def get_assistant_service() -> AssistantService:
    return assistant_service


def agent_startup_failure_detail(
    exc: AgentStartupError,
    *,
    project_name: str,
    session_id: str | None,
    title: str,
) -> dict:
    """把 runtime 启动异常映射为两个 Agent API 共用的结构化错误。"""
    original = exc.__cause__ or exc
    failure = exc.failure_observation or build_startup_failure_observation(
        original,
        project_name=project_name,
        session_id=session_id,
        sdk_stderr=exc.sdk_stderr,
    )
    return {"code": "agent_startup_failed", "message": title, "failure": failure}


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
    data: str = Field(max_length=MAX_IMAGE_BASE64_CHARS)
    media_type: str

    @field_validator("data", mode="before")
    @classmethod
    def validate_data_size(cls, value: object) -> object:
        if isinstance(value, str) and len(value) > MAX_IMAGE_BASE64_CHARS:
            raise PydanticCustomError(
                "assistant_image_too_large", "assistant image exceeds the base64 character budget"
            )
        return value


class ImageRequest(BaseModel):
    images: list[ImageAttachment] = Field(default_factory=list, max_length=MAX_IMAGES_PER_REQUEST)

    @model_validator(mode="before")
    @classmethod
    def validate_total_image_size(cls, value: object) -> object:
        images = value.get("images", []) if isinstance(value, dict) else []
        total_chars = sum(
            len(image.get("data", ""))
            for image in images
            if isinstance(image, dict) and isinstance(image.get("data", ""), str)
        )
        if total_chars > MAX_IMAGES_TOTAL_BASE64_CHARS:
            raise PydanticCustomError(
                "assistant_images_total_too_large",
                "assistant images exceed the total base64 character budget",
            )
        return value


class SendRequest(ImageRequest):
    content: str = ""
    session_id: str | None = None
    # 请求侧幂等键：同键重试返回既有权威条目，不产生重复。
    client_key: str | None = Field(default=None, max_length=128)


class RewriteRequest(ImageRequest):
    # 锚点：被改写的那条用户消息在事件日志里的条目 uuid。
    anchor_entry_uuid: str = Field(min_length=1, max_length=256)
    content: str = ""
    client_key: str | None = Field(default=None, max_length=128)


class AnswerQuestionRequest(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)


@router.post("/sessions/send")
async def send_message(
    project_name: str,
    req: SendRequest,
    request: Request,
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
            client_key=req.client_key,
        )
        return result
    except SessionCapacityError as exc:
        raise ServiceUnavailableError("session_capacity_exceeded") from exc
    except FileNotFoundError as exc:
        raise NotFoundError("session_or_project_not_found") from exc
    except TimeoutError:
        raise HTTPException(status_code=504, detail=_t("sdk_session_timeout"))
    except SessionBusyError as exc:
        logger.warning("会话发送请求冲突: %s", exc)
        raise ConflictError("session_busy") from exc
    except ValueError as exc:
        # 空消息内容 / 非法项目名等坏请求，str(exc) 只进日志
        logger.warning("会话发送请求非法: %s", exc)
        raise BadRequestError("request_invalid") from exc
    except AgentStartupError as exc:
        raise HTTPException(
            status_code=502,
            detail=agent_startup_failure_detail(
                exc,
                project_name=project_name,
                session_id=req.session_id,
                title=_t("agent_startup_failed_title"),
            ),
        )
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


@router.post("/sessions/{session_id}/rewrite")
async def rewrite_message(
    project_name: str,
    session_id: str,
    req: RewriteRequest,
    request: Request,
    _t: Translator,
):
    """改写会话中某条历史用户消息：分叉出新会话并在其上重跑。

    ``session_id`` 是被改写的原会话；响应里的 ``session_id`` 是承接改写的新会话，
    前端据此整体切换过去。运行中的会话由端点自动中断，调用方不必先停止。
    """
    try:
        service = get_assistant_service()
        return await service.rewrite_message(
            project_name,
            session_id,
            anchor_entry_uuid=req.anchor_entry_uuid,
            content=req.content,
            images=req.images,
            locale=get_locale(request),
            client_key=req.client_key,
        )
    except RewriteAnchorError as exc:
        raise BadRequestError("rewrite_anchor_invalid") from exc
    except PendingQuestionError as exc:
        raise ConflictError("rewrite_blocked_by_question") from exc
    except SessionSupersededError as exc:
        raise ConflictError("session_already_superseded") from exc
    except RewriteUnavailableError as exc:
        raise ServiceUnavailableError("rewrite_unavailable") from exc
    except InterruptSettleTimeoutError:
        raise HTTPException(status_code=504, detail=_t("rewrite_interrupt_timeout"))
    except SessionBranchError as exc:
        logger.warning("分支会话建立失败: %s", exc)
        raise HTTPException(status_code=500, detail=_t("rewrite_failed"))
    except SessionCapacityError as exc:
        raise ServiceUnavailableError("session_capacity_exceeded") from exc
    except FileNotFoundError as exc:
        raise NotFoundError("session_or_project_not_found") from exc
    except TimeoutError:
        raise HTTPException(status_code=504, detail=_t("sdk_session_timeout"))
    except SessionBusyError as exc:
        logger.warning("会话改写请求冲突: %s", exc)
        raise ConflictError("session_busy") from exc
    except ValueError as exc:
        logger.warning("会话改写请求非法: %s", exc)
        raise BadRequestError("request_invalid") from exc
    except AgentStartupError as exc:
        raise HTTPException(
            status_code=502,
            detail=agent_startup_failure_detail(
                exc,
                project_name=project_name,
                session_id=session_id,
                title=_t("agent_startup_failed_title"),
            ),
        )
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


@router.get("/sessions")
async def list_sessions(
    project_name: str,
    _t: Translator,
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
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


@router.get("/sessions/{session_id}")
async def get_session(project_name: str, session_id: str, _t: Translator):
    try:
        service = get_assistant_service()
        session = await _validate_session_ownership(service, session_id, project_name, _t)
        return session.model_dump()
    except HTTPException:
        raise
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


@router.delete("/sessions/{session_id}")
async def delete_session(project_name: str, session_id: str, _t: Translator):
    try:
        service = get_assistant_service()
        await _validate_session_ownership(service, session_id, project_name, _t)
        deleted = await service.delete_session(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=_t("session_not_found", session_id=session_id))
        return {"success": True}
    except HTTPException:
        raise
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


@router.get("/sessions/{session_id}/messages")
async def list_messages(project_name: str, session_id: str, _t: Translator):
    raise HTTPException(
        status_code=410,
        detail=_t("interface_offline"),
    )


@router.get("/sessions/{session_id}/snapshot")
async def get_snapshot(project_name: str, session_id: str, _t: Translator):
    raise HTTPException(
        status_code=410,
        detail=_t("interface_offline"),
    )


@router.get("/sessions/{session_id}/entries")
async def list_entries(
    project_name: str,
    session_id: str,
    _t: Translator,
    after: int = Query(default=-1, ge=-1),
):
    """冷读会话事件日志（历史回放；``after`` 为 seq 游标）。"""
    try:
        service = get_assistant_service()
        meta = await _validate_session_ownership(service, session_id, project_name, _t)
        return await service.list_session_entries(session_id, meta=meta, after_seq=after)
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise NotFoundError("session_not_found", session_id=session_id) from exc
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


@self_auth_router.get("/sessions/{session_id}/entries/stream", response_class=EventSourceResponse)
async def stream_entries(
    project_name: str,
    session_id: str,
    request: Request,
    _user: CurrentUserFlexible,
    _t: Translator,
    after: int = Query(default=-1, ge=-1),
    deps: tuple[AssistantService, SessionMeta] = Depends(_assistant_service_for_stream),
) -> AsyncIterator[ServerSentEvent]:
    """SSE entry 流：事件 id 即 seq。

    游标优先级：EventSource 自动重连携带的 ``Last-Event-ID`` > ``?after=<seq>``
    （冷订阅）。
    """
    service, meta = deps
    cursor = after
    last_event_id = request.headers.get("last-event-id", "").strip()
    if last_event_id:
        try:
            cursor = int(last_event_id)
        except ValueError:
            logger.debug("忽略无效的 Last-Event-ID: %r，回退到游标 %s", last_event_id, cursor)
    try:
        async for event in service.stream_entry_events(session_id, meta=meta, request=request, after_seq=cursor):
            yield event
    except HTTPException:
        raise
    except AgentStartupError as exc:
        detail = agent_startup_failure_detail(
            exc,
            project_name=project_name,
            session_id=session_id,
            title=_t("agent_startup_failed_title"),
        )
        async for event in service.stream_startup_failure_events(session_id, detail["failure"]):
            yield event
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(project_name: str, session_id: str, _t: Translator):
    try:
        service = get_assistant_service()
        meta = await _validate_session_ownership(service, session_id, project_name, _t)
        result = await service.interrupt_session(session_id, meta=meta)
        return result
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise NotFoundError("session_not_found", session_id=session_id) from exc
    except ValueError as exc:
        logger.warning("会话中断请求非法: %s", exc)
        raise BadRequestError("request_invalid") from exc
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


@router.post("/sessions/{session_id}/questions/{question_id}/answer")
async def answer_question(
    project_name: str,
    session_id: str,
    question_id: str,
    req: AnswerQuestionRequest,
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
    except FileNotFoundError as exc:
        raise NotFoundError("session_not_found", session_id=session_id) from exc
    except ValueError as exc:
        # 会话未运行或无待回答问题
        logger.warning("会话回答请求非法: %s", exc)
        raise BadRequestError("session_question_unavailable") from exc
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


@self_auth_router.get("/sessions/{session_id}/stream")
async def stream_events(project_name: str, session_id: str, _user: CurrentUserFlexible, _t: Translator):
    raise HTTPException(
        status_code=410,
        detail=_t("interface_offline"),
    )


@router.get("/skills")
async def list_skills(project_name: str, _t: Translator):
    try:
        skills = get_assistant_service().list_available_skills(project_name=project_name)
        return {"skills": skills}
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))
