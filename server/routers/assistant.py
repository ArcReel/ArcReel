"""
Assistant session APIs.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field, field_validator, model_validator

# Top-level pydantic does not re-export PydanticCustomError; depend directly on pydantic-core.
from pydantic_core import PydanticCustomError

from lib import PROJECT_ROOT
from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError, ServiceUnavailableError
from server.agent_runtime.failure_observation import build_startup_failure_observation
from server.agent_runtime.models import SessionMeta, WithdrawalIntent
from server.agent_runtime.service import (
    AssistantService,
    InterruptSettleTimeoutError,
    PendingQuestionError,
    QueuedMessagesPendingError,
    RewriteAnchorError,
    RewriteUnavailableError,
    SessionSupersededError,
)
from server.agent_runtime.session_branch import SessionBranchError
from server.agent_runtime.session_manager import (
    AgentStartupError,
    QueuedMessageNotFoundError,
    QueuedMessageWithdrawalPendingError,
    SessionCapacityError,
    UnrecordedMessageError,
)
from server.i18n import Translator, get_locale

router = APIRouter()

assistant_service = AssistantService(project_root=PROJECT_ROOT)

MAX_IMAGE_SOURCE_BYTES = 5 * 1024 * 1024
# Frontend attachments are resized/reencoded within this budget; derive the server total from it, retaining 5 MB per image
# as a fallback when bypassing frontend checks. Total 5 × 1.5 MB is about 10 MB base64, below Messages API limits
# of 32 MB per request (Bedrock 20 MB / Vertex 30 MB).
MAX_RESIZED_IMAGE_SOURCE_BYTES = 3 * 1024 * 1024 // 2
# Base64 needs four characters for every three source bytes, rounded up to a full quartet.
MAX_IMAGE_BASE64_CHARS = 4 * ((MAX_IMAGE_SOURCE_BYTES + 2) // 3)
MAX_RESIZED_IMAGE_BASE64_CHARS = 4 * ((MAX_RESIZED_IMAGE_SOURCE_BYTES + 2) // 3)
MAX_IMAGES_PER_REQUEST = 5
MAX_IMAGES_TOTAL_BASE64_CHARS = MAX_IMAGES_PER_REQUEST * MAX_RESIZED_IMAGE_BASE64_CHARS


def get_assistant_service() -> AssistantService:
    return assistant_service


def agent_startup_failure_detail(
    exc: AgentStartupError,
    *,
    project_name: str,
    session_id: str | None,
    title: str,
) -> dict:
    """Map runtime startup exceptions to structured errors shared by both Agent APIs."""
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
    # Request idempotency key: retries with the same key return the existing authoritative entry without duplicates.
    client_key: str | None = Field(default=None, max_length=128)


class RewriteRequest(ImageRequest):
    # Anchor: the rewritten user message’s entry uuid in the event log.
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
        return await service.send_or_create(
            project_name,
            req.content,
            session_id=req.session_id,
            images=req.images,
            locale=get_locale(request),
            client_key=req.client_key,
        )
    except SessionSupersededError as exc:
        raise ConflictError("session_already_superseded") from exc
    except UnrecordedMessageError as exc:
        raise ConflictError("message_accepted_but_unrecorded") from exc
    except SessionCapacityError as exc:
        raise ServiceUnavailableError("session_capacity_exceeded") from exc
    except FileNotFoundError as exc:
        raise NotFoundError("session_or_project_not_found") from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=_t("sdk_session_timeout")) from exc
    except ValueError as exc:
        # For empty messages, invalid project names and other bad requests, str(exc) goes only to logs.
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
        ) from exc
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post("/sessions/{session_id}/rewrite")
async def rewrite_message(
    project_name: str,
    session_id: str,
    req: RewriteRequest,
    request: Request,
    _t: Translator,
):
    """Rewrite a historical user message by forking a new session and rerunning there.

    Input ``session_id`` identifies the original session; response ``session_id`` identifies the new session carrying the rewrite,
    letting the frontend switch completely. The endpoint interrupts active sessions automatically; callers need not stop them first."""
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
    except QueuedMessagesPendingError as exc:
        raise ConflictError("rewrite_blocked_by_queued_messages") from exc
    except SessionSupersededError as exc:
        raise ConflictError("session_already_superseded") from exc
    except RewriteUnavailableError as exc:
        raise ServiceUnavailableError("rewrite_unavailable") from exc
    except InterruptSettleTimeoutError as exc:
        raise HTTPException(status_code=504, detail=_t("rewrite_interrupt_timeout")) from exc
    except SessionBranchError as exc:
        logger.warning("分支会话建立失败: %s", exc)
        raise HTTPException(status_code=500, detail=_t("rewrite_failed")) from exc
    except SessionCapacityError as exc:
        raise ServiceUnavailableError("session_capacity_exceeded") from exc
    except FileNotFoundError as exc:
        raise NotFoundError("session_or_project_not_found") from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=_t("sdk_session_timeout")) from exc
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
        ) from exc
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


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
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get("/sessions/{session_id}")
async def get_session(project_name: str, session_id: str, _t: Translator):
    try:
        service = get_assistant_service()
        session = await _validate_session_ownership(service, session_id, project_name, _t)
        return session.model_dump()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


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
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


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
    """Read stored session events for history replay, with ``after`` as the seq cursor."""
    try:
        service = get_assistant_service()
        meta = await _validate_session_ownership(service, session_id, project_name, _t)
        return await service.list_session_entries(session_id, meta=meta, after_seq=after)
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise NotFoundError("session_not_found", session_id=session_id) from exc
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get("/sessions/{session_id}/entries/stream", response_class=EventSourceResponse)
async def stream_entries(
    project_name: str,
    session_id: str,
    request: Request,
    _t: Translator,
    after: int = Query(default=-1, ge=-1),
    deps: tuple[AssistantService, SessionMeta] = Depends(_assistant_service_for_stream),
) -> AsyncIterator[ServerSentEvent]:
    """SSE entry stream: event IDs are seq values.

    Cursor priority: ``Last-Event-ID`` from frontend streaming reconnection overrides ``?after=<seq>`` for initial subscriptions."""
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
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(project_name: str, session_id: str, _t: Translator):
    try:
        service = get_assistant_service()
        meta = await _validate_session_ownership(service, session_id, project_name, _t)
        return await service.interrupt_session(session_id, meta=meta)
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise NotFoundError("session_not_found", session_id=session_id) from exc
    except ValueError as exc:
        logger.warning("会话中断请求非法: %s", exc)
        raise BadRequestError("request_invalid") from exc
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


async def _act_on_queued_message(
    project_name: str, session_id: str, action: Callable[[AssistantService], Awaitable[dict]], _t: Translator
) -> dict:
    """Shared error mapping for queued-message editing, deletion, resending and immediate sending.

    Resending or immediately sending unsent messages reconnects when the CLI has exited, potentially hitting capacity or startup failures."""
    try:
        return await action(get_assistant_service())
    except QueuedMessageNotFoundError as exc:
        raise NotFoundError("queued_message_not_found") from exc
    except QueuedMessageWithdrawalPendingError as exc:
        raise ConflictError("queued_message_withdrawal_pending") from exc
    except SessionCapacityError as exc:
        raise ServiceUnavailableError("session_capacity_exceeded") from exc
    except FileNotFoundError as exc:
        raise NotFoundError("session_not_found", session_id=session_id) from exc
    except AgentStartupError as exc:
        raise HTTPException(
            status_code=502,
            detail=agent_startup_failure_detail(
                exc,
                project_name=project_name,
                session_id=session_id,
                title=_t("agent_startup_failed_title"),
            ),
        ) from exc
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


async def _withdraw_queued_message(
    project_name: str, session_id: str, message_id: str, intent: WithdrawalIntent, _t: Translator
) -> dict:
    return await _act_on_queued_message(
        project_name,
        session_id,
        lambda service: service.withdraw_queued_message(project_name, session_id, message_id, intent=intent),
        _t,
    )


@router.post("/sessions/{session_id}/queued-messages/{message_id}/edit")
async def edit_queued_message(project_name: str, session_id: str, message_id: str, _t: Translator):
    """Return a queued message to the input box; successful withdrawal returns its content in ``message``.

    ``outcome`` ``withdrawn`` means removed from the queue; ``accepted`` means the Agent already received it and it enters the conversation normally."""
    return await _withdraw_queued_message(project_name, session_id, message_id, "edit", _t)


@router.delete("/sessions/{session_id}/queued-messages/{message_id}")
async def delete_queued_message(project_name: str, session_id: str, message_id: str, _t: Translator):
    """Delete a queued message by discarding after successful withdrawal. Match the edit response shape with ``message`` always null."""
    return await _withdraw_queued_message(project_name, session_id, message_id, "delete", _t)


@router.post("/sessions/{session_id}/queued-messages/{message_id}/resend")
async def resend_queued_message(project_name: str, session_id: str, message_id: str, request: Request, _t: Translator):
    """Resend an unsent message to the Agent, reconnecting interrupted sessions first; return the queued message in ``queued_message``."""
    return await _act_on_queued_message(
        project_name,
        session_id,
        lambda service: service.resend_queued_message(project_name, session_id, message_id, locale=get_locale(request)),
        _t,
    )


@router.post("/sessions/{session_id}/queued-messages/{message_id}/send-now")
async def send_queued_message_now(project_name: str, session_id: str, message_id: str, _t: Translator):
    """Immediately send a queued message, interrupting the current Agent turn to handle it first.

    ``outcome`` ``sent`` means resubmitted, remaining in the tray until accepted; ``accepted`` means already received by the Agent, without resending."""
    return await _act_on_queued_message(
        project_name,
        session_id,
        lambda service: service.send_queued_message_now(project_name, session_id, message_id),
        _t,
    )


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
        return await service.answer_user_question(
            session_id=session_id,
            question_id=question_id,
            answers=req.answers,
            meta=meta,
        )
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise NotFoundError("session_not_found", session_id=session_id) from exc
    except ValueError as exc:
        # Session is not running or has no pending question.
        logger.warning("会话回答请求非法: %s", exc)
        raise BadRequestError("session_question_unavailable") from exc
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get("/sessions/{session_id}/stream")
async def stream_events(project_name: str, session_id: str, _t: Translator):
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
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
