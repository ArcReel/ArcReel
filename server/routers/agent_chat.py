"""
Synchronous Agent chat endpoint

Wraps the existing SSE streaming assistant as a synchronous request-response mode
for use by external agents such as OpenClaw.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.agent_runtime.service import AssistantService
from server.agent_runtime.session_manager import SessionCapacityError
from server.auth import CurrentUser
from server.routers.assistant import get_assistant_service

logger = logging.getLogger(__name__)

router = APIRouter()

SYNC_CHAT_TIMEOUT = 120  # seconds


class AgentChatRequest(BaseModel):
    project_name: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    message: str = Field(min_length=1)
    session_id: str | None = None


class AgentChatResponse(BaseModel):
    session_id: str
    reply: str
    status: str  # "completed" | "timeout" | "error"


def _extract_text_from_assistant_message(msg: dict) -> str:
    """Extract plain text content from an assistant-type message."""
    content = msg.get("content", [])
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if text and isinstance(text, str):
            parts.append(text)
    return "".join(parts)


TERMINAL_RUNTIME_STATUSES = {"idle", "completed", "error", "interrupted"}


async def _collect_reply(
    service: AssistantService,
    session_id: str,
    timeout: float,
) -> tuple[str, str]:
    """Subscribe to the session queue and collect assistant replies until completion or timeout.

    Returns:
        (reply_text, status) — status is "completed" / "timeout" / "error"
    """
    queue = await service.session_manager.subscribe(session_id, replay_buffer=True)
    try:
        reply_parts: list[str] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                status = "timeout"
                break

            try:
                message = await asyncio.wait_for(queue.get(), timeout=min(remaining, 5.0))
            except TimeoutError:
                # Check whether the session has already completed
                live_status = await service.session_manager.get_status(session_id)
                if live_status and live_status != "running":
                    status = "completed" if live_status in {"idle", "completed"} else live_status
                    break
                # Check whether the deadline has been reached
                if loop.time() >= deadline:
                    status = "timeout"
                    break
                continue

            msg_type = message.get("type", "")

            if msg_type == "assistant":
                text = _extract_text_from_assistant_message(message)
                if text:
                    reply_parts.append(text)

            elif msg_type == "result":
                # Terminal message: extract the last assistant reply (if not already received from queue)
                subtype = str(message.get("subtype") or "").lower()
                is_error = bool(message.get("is_error"))
                if is_error or subtype.startswith("error"):
                    status = "error"
                else:
                    status = "completed"
                break

            elif msg_type == "runtime_status":
                runtime_status = str(message.get("status") or "").strip()
                if runtime_status in TERMINAL_RUNTIME_STATUSES and runtime_status != "running":
                    status = "completed" if runtime_status in {"idle", "completed"} else runtime_status
                    break

            elif msg_type == "_queue_overflow":
                # Queue overflow — abort
                status = "error"
                break

        return "".join(reply_parts), status

    finally:
        await service.session_manager.unsubscribe(session_id, queue)


@router.post("/agent/chat")
async def agent_chat(
    body: AgentChatRequest,
    _user: CurrentUser,
) -> AgentChatResponse:
    """Synchronous Agent chat endpoint.

    - If session_id is not provided, a new session is created
    - If session_id is provided, the conversation continues in that session context
    - Internally delegates to AssistantService, collects the full response, and returns it
    - Returns the partial response collected so far with status "timeout" after 120 seconds
    """
    service = get_assistant_service()

    # Verify that the project exists
    try:
        service.pm.get_project_path(body.project_name)
    except (FileNotFoundError, KeyError):
        raise HTTPException(status_code=404, detail=f"Project '{body.project_name}' does not exist")

    # If session_id is provided, validate session ownership first
    if body.session_id:
        session = await service.get_session(body.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session '{body.session_id}' does not exist")
        if session.project_name != body.project_name:
            raise HTTPException(
                status_code=400,
                detail=f"Session '{body.session_id}' belongs to project '{session.project_name}', which does not match the requested project '{body.project_name}'",
            )

    # Uniformly create or reuse a session and send the message via send_or_create.
    # Relies on replay_buffer=True buffering already-sent messages; no race condition.
    try:
        result = await service.send_or_create(
            body.project_name,
            body.message,
            session_id=body.session_id,
        )
        session_id = result["session_id"]
    except SessionCapacityError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except TimeoutError:
        raise HTTPException(status_code=504, detail="SDK session creation timed out")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Collect reply (with timeout)
    reply, status = await _collect_reply(service, session_id, SYNC_CHAT_TIMEOUT)

    # If no text was received but a snapshot is available, extract the latest assistant reply from it
    if not reply:
        try:
            snapshot = await service.get_snapshot(session_id)
            turns = snapshot.get("turns", [])
            for turn in reversed(turns):
                if turn.get("role") == "assistant":
                    blocks = turn.get("content", [])
                    text_parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
                    reply = "".join(text_parts)
                    if reply:
                        break
        except Exception as exc:
            logger.warning("Failed to get snapshot session_id=%s: %s", session_id, exc)

    return AgentChatResponse(
        session_id=session_id,
        reply=reply,
        status=status,
    )
