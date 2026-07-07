"""
同步 Agent 对话端点

封装现有 SSE 流式助手为同步请求-响应模式，供 OpenClaw 等外部 Agent 调用。
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from lib.i18n import Translator, get_locale
from server.agent_runtime.models import Heartbeat, LiveMessage
from server.agent_runtime.service import AssistantService
from server.agent_runtime.session_manager import SessionCapacityError
from server.auth import CurrentUser
from server.routers.assistant import get_assistant_service

logger = logging.getLogger(__name__)

router = APIRouter()

SYNC_CHAT_TIMEOUT = 120  # 秒


class AgentChatRequest(BaseModel):
    project_name: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    message: str = Field(min_length=1)
    session_id: str | None = None


class AgentChatResponse(BaseModel):
    session_id: str
    reply: str
    status: str  # "completed" | "timeout" | "error" | "interrupted"
    truncated: bool = False  # 回复可能不完整且未能从事件日志确认补齐


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


def _consume_message(message: dict, reply_parts: list[str]) -> str | None:
    """处理一条消息：收集 assistant 文本；命中终结条件时返回终态 status，否则返回 None。

    直播消息与历史事件日志条目走同一套文本收集规则。
    """
    msg_type = message.get("type", "")

    if msg_type == "assistant":
        text = _extract_text_from_assistant_message(message)
        if text:
            reply_parts.append(text)

    elif msg_type == "result":
        subtype = str(message.get("subtype") or "").lower()
        is_error = bool(message.get("is_error"))
        return "error" if is_error or subtype.startswith("error") else "completed"

    elif msg_type == "runtime_status":
        runtime_status = str(message.get("status") or "").strip()
        if runtime_status in TERMINAL_RUNTIME_STATUSES and runtime_status != "running":
            return "completed" if runtime_status in {"idle", "completed"} else runtime_status

    return None


async def _collect_reply(
    service: AssistantService,
    session_id: str,
    timeout: float,
) -> tuple[str, str]:
    """消费会话消息流，收集 assistant 回复直到完成或超时。

    通过 ``stream_messages`` 上下文管理器消费（非 SSE、无 ``request`` 对象）：
    会话状态判断挂在心跳事件上，超时检测粒度因此变为 idle_timeout（≤5s）。
    退出注销由 ``__aexit__`` 确定性承载（见 ADR-0005）。订阅建立前已广播的
    消息不会重放——漏收的回复由调用方从事件日志兜底提取。

    Returns:
        (reply_text, status) — status 为 "completed" / "timeout" / "error"
    """
    reply_parts: list[str] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    status = "timeout"

    async with service.session_manager.stream_messages(session_id, idle_timeout=5.0) as stream:
        async for event in stream:
            # deadline 必须每轮都查：持续 <idle_timeout 间隔的消息流会让心跳永不触发，
            # 若只在心跳上判超时，跑飞/刷屏的会话会让本同步请求无界挂起。
            if loop.time() >= deadline:
                status = "timeout"
                break

            if isinstance(event, Heartbeat):
                # 无 request 对象：在心跳事件上判会话状态（deadline 已在循环顶部统一判）。
                live_status = await service.session_manager.get_status(session_id)
                if live_status and live_status != "running":
                    status = "completed" if live_status in {"idle", "completed"} else live_status
                    break
                continue

            if not isinstance(event, LiveMessage):
                continue

            terminal = _consume_message(event.message, reply_parts)
            if terminal is not None:
                status = terminal
                break
        else:
            # 订阅队列溢出以流结束表达：显式收尾（不再傻等到超时）。
            status = "error"

    return "".join(reply_parts), status


def _extract_reply_from_entries(entries: list[dict], after_seq: int) -> str:
    """从事件日志条目提取主线 assistant 回复文本（本轮用户条目之后）。"""
    parts: list[str] = []
    for entry in entries:
        if entry.get("type") != "assistant" or entry.get("parent_tool_use_id"):
            continue
        seq = entry.get("seq")
        if isinstance(seq, int) and seq <= after_seq:
            continue
        content = entry.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    return "".join(parts)


@router.post("/agent/chat")
async def agent_chat(
    body: AgentChatRequest,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
) -> AgentChatResponse:
    """同步 Agent 对话端点。

    - 若不传 session_id，则新建会话
    - 若传入 session_id，则在该会话上下文中继续对话
    - 内部对接 AssistantService，收集完整响应后返回
    - 超过 120 秒返回已收集的部分响应，status 为 "timeout"
    - 流异常收尾时从事件日志补齐回复；无法确认完整的非空回复以 truncated=true 标记
    """
    service = get_assistant_service()

    # 验证项目是否存在
    try:
        service.pm.get_project_path(body.project_name)
    except (FileNotFoundError, KeyError):
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=body.project_name))

    # 若传入 session_id，先校验会话归属
    if body.session_id:
        session = await service.get_session(body.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=_t("session_not_found", session_id=body.session_id))
        if session.project_name != body.project_name:
            raise HTTPException(
                status_code=400,
                detail=_t(
                    "session_project_mismatch",
                    session_id=body.session_id,
                    session_project=session.project_name,
                    request_project=body.project_name,
                ),
            )

    # 统一通过 send_or_create 创建或复用会话并发送消息。
    try:
        result = await service.send_or_create(
            body.project_name,
            body.message,
            session_id=body.session_id,
            locale=get_locale(request),
        )
        session_id = result["session_id"]
    except SessionCapacityError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except TimeoutError:
        raise HTTPException(status_code=504, detail=_t("sdk_session_timeout"))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # 收集回复（带超时）
    reply, status = await _collect_reply(service, session_id, SYNC_CHAT_TIMEOUT)
    truncated = False

    # 事件日志是唯一可靠读源，两类情形需要回读兜底：
    # - 直播收集为空：订阅建立前回复已完成，或部分广播已错过；
    # - 流异常收尾（error / interrupted，如订阅队列溢出）：直播回复可能非空但被截断，
    #   不能当作完整回复放行——回读补齐，补不齐则标记截断态。
    stream_aborted = status not in ("completed", "timeout")
    if not reply or stream_aborted:
        try:
            user_entry = result.get("entry")
            user_seq = user_entry.get("seq", -1) if isinstance(user_entry, dict) else -1
            payload = await service.list_session_entries(session_id, after_seq=user_seq)
            log_reply = _extract_reply_from_entries(payload.get("entries", []), user_seq)
            session_running = payload.get("status") == "running"
            if not reply:
                # 直播收集为空：按既有兜底语义采用日志内容（日志同样为空则维持空回复）；
                # 但异常收尾且会话仍在 running 时，日志内容同样未收全，不能当作已确认完整。
                reply = log_reply
                truncated = stream_aborted and session_running and bool(log_reply)
            else:
                # 流异常收尾但直播已收到非空文本：会话运行态标志本身存在竞态——
                # 中断/取消路径会丢弃已广播但未及落库的尾部消息，异步落库也可能滞后
                # 于直播广播——单凭"非 running"不足以证明日志已收全本轮内容。
                # 仅当会话已转终态、且日志文本不短于直播已收文本时才视为确认完整，
                # 可放心整体替换；否则保留直播文本，标记截断态，避免被更短、更旧
                # 的日志内容静默覆盖。
                if not session_running and len(log_reply) >= len(reply):
                    reply = log_reply
                else:
                    truncated = stream_aborted
        except Exception as exc:
            logger.warning("从事件日志提取回复失败 session_id=%s: %s", session_id, exc)
            truncated = stream_aborted and bool(reply)

    return AgentChatResponse(
        session_id=session_id,
        reply=reply,
        status=status,
        truncated=truncated,
    )
