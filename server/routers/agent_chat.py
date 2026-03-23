"""
同步 Agent 对话端点

封装现有 SSE 流式助手为同步请求-响应模式，供 OpenClaw 等外部 Agent 调用。
"""

import asyncio
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from server.agent_runtime.service import AssistantService
from server.agent_runtime.session_manager import SessionCapacityError
from server.auth import get_current_user
from server.routers.assistant import get_assistant_service

logger = logging.getLogger(__name__)

router = APIRouter()

SYNC_CHAT_TIMEOUT = 120  # 秒


class AgentChatRequest(BaseModel):
    project_name: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    message: str = Field(min_length=1)
    session_id: Optional[str] = None


class AgentChatResponse(BaseModel):
    session_id: str
    reply: str
    status: str  # "completed" | "timeout" | "error"


def _extract_text_from_assistant_message(msg: dict) -> str:
    """从 assistant 类型消息中提取纯文本内容。"""
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
    """订阅会话队列，收集 assistant 回复直到完成或超时。

    Returns:
        (reply_text, status) — status 为 "completed" / "timeout" / "error"
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
            except asyncio.TimeoutError:
                # 检查会话是否已完成
                live_status = await service.session_manager.get_status(session_id)
                if live_status and live_status != "running":
                    status = "completed" if live_status in {"idle", "completed"} else live_status
                    break
                # 检查是否超时
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
                # 终结消息：提取最后一条 assistant 回复（如果还没有从队列里收到）
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
                # 队列溢出，中断
                status = "error"
                break

        return "".join(reply_parts), status

    finally:
        await service.session_manager.unsubscribe(session_id, queue)


@router.post("/agent/chat")
async def agent_chat(
    body: AgentChatRequest,
    _user: Annotated[dict, Depends(get_current_user)],
) -> AgentChatResponse:
    """同步 Agent 对话端点。

    - 若不传 session_id，则新建会话
    - 若传入 session_id，则在该会话上下文中继续对话
    - 内部对接 AssistantService，收集完整响应后返回
    - 超过 120 秒返回已收集的部分响应，status 为 "timeout"
    """
    service = get_assistant_service()

    # 验证项目是否存在
    try:
        service.pm.get_project_path(body.project_name)
    except (FileNotFoundError, KeyError):
        raise HTTPException(status_code=404, detail=f"项目 '{body.project_name}' 不存在")

    # 若传入 session_id，先校验会话归属
    if body.session_id:
        session = await service.get_session(body.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"会话 '{body.session_id}' 不存在")
        if session.project_name != body.project_name:
            raise HTTPException(
                status_code=400,
                detail=f"会话 '{body.session_id}' 属于项目 '{session.project_name}'，与请求项目 '{body.project_name}' 不符",
            )

    # 统一通过 send_or_create 创建或复用会话并发送消息。
    # 依赖 replay_buffer=True 缓冲已发送的消息，不会产生竞争条件。
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
        raise HTTPException(status_code=504, detail="SDK 会话创建超时")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # 收集回复（带超时）
    reply, status = await _collect_reply(service, session_id, SYNC_CHAT_TIMEOUT)

    # 若未收到文本但有快照，从 snapshot 提取最新助手回复
    if not reply:
        try:
            snapshot = await service.get_snapshot(session_id)
            turns = snapshot.get("turns", [])
            for turn in reversed(turns):
                if turn.get("role") == "assistant":
                    blocks = turn.get("content", [])
                    text_parts = [
                        b.get("text", "") for b in blocks
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    reply = "".join(text_parts)
                    if reply:
                        break
        except Exception as exc:
            logger.warning("获取快照失败 session_id=%s: %s", session_id, exc)

    return AgentChatResponse(
        session_id=session_id,
        reply=reply,
        status=status,
    )
