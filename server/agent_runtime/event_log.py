"""会话事件日志 — UI 时间线唯一读源。

条目在写入点定型：SDK 消息流在入日志一处完成规范化（tool_result 定型为独立
条目、subagent 消息带 parent_tool_use_id 标记、stream_event 不进日志），
渲染端与读取端不做语义嗅探、不做去重。日志是 SDK transcript 的物化视图：
仅有 transcript 的旧会话首次访问时重放重建（懒生成）。
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import weakref
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import delete as sa_delete
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError

from lib.db import safe_session_factory
from lib.db.base import DEFAULT_USER_ID, utc_now
from lib.db.models.session_event import AgentSessionEventLogEntry
from server.agent_runtime.message_serialization import utc_now_iso
from server.agent_runtime.turn_schema import _stringify_content, normalize_content

logger = logging.getLogger(__name__)

ENTRY_TYPE_USER = "user"
ENTRY_TYPE_ASSISTANT = "assistant"
ENTRY_TYPE_TOOL_RESULT = "tool_result"
ENTRY_TYPE_SYSTEM = "system"

ENTRY_SUBTYPE_INTERRUPT = "interrupt"
ENTRY_SUBTYPE_TASK_NOTIFICATION = "task_notification"
ENTRY_SUBTYPE_QUESTION_ANSWER = "question_answer"

SYSTEM_SUBTYPE_SKILL_INVOCATION = "skill_invocation"

_TASK_SUBTYPES = {"task_started", "task_progress", "task_notification"}

# skill 注入用户消息的识别前缀。语义嗅探只允许发生在写入点这一处：
# 定型后日志只存 skill 名与入参，读取端与渲染端不再接触注入文本。
_SKILL_INJECTION_PREFIXES = ("Base directory for this skill:", "Skill content:")

# SDK 消息与 transcript 载荷对 subagent 归属键的大小写变体，
# 写入点统一归一化为 parent_tool_use_id。
_PARENT_KEY_VARIANTS = ("parent_tool_use_id", "parentToolUseID", "parentToolUseId")

# CLI 注入的中断回显前缀。具体措辞是 CLI 内部实现细节（非稳定 API），
# 只在写入点做一次前缀识别，定型后读取端不再嗅探。
_INTERRUPT_ECHO_PREFIX = "[Request interrupted"

# SDK 以用户消息形态注入的后台任务通知 XML。
_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>\s*.*?</task-notification>", re.DOTALL)

_ASK_USER_QUESTION_TOOL = "askuserquestion"

# 与 DbSessionStore.append 相同的 seq 竞争重试参数。
_MAX_APPEND_RETRY = 16
_APPEND_BACKOFF_CAP_S = 0.05

# 标记 SDK 回放的用户消息副本（POST 受理时已写日志），供写入点跳过。
# 只存活在进程内消息 dict 上，不落任何持久化层。
REPLAYED_USER_ECHO_KEY = "_replayed_user_echo"


def _extract_parent(message: dict[str, Any]) -> str | None:
    """写入点归一化 subagent 归属键（大小写变体只在此处识别）。"""
    for key in _PARENT_KEY_VARIANTS:
        parent = message.get(key)
        if isinstance(parent, str) and parent.strip():
            return parent
    return None


def _copy_parent(message: dict[str, Any], entry: dict[str, Any]) -> None:
    parent = _extract_parent(message)
    if parent:
        entry["parent_tool_use_id"] = parent


def _is_skill_injection_text(text: str) -> bool:
    return text.startswith(_SKILL_INJECTION_PREFIXES)


def _parse_skill_name_from_injection(text: str) -> str | None:
    """从注入首行的 skill 基目录路径提取名称（无先行 Skill tool_use 时的兜底）。"""
    first_line = text.split("\n", 1)[0].strip()
    prefix = "Base directory for this skill:"
    if not first_line.startswith(prefix):
        return None
    path = first_line[len(prefix) :].strip().replace("\\", "/").rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if segments and segments[-1].upper() == "SKILL.MD":
        segments = segments[:-1]
    return segments[-1] if segments else None


def build_interrupt_entry(
    *,
    uuid: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """构造中断的 typed 条目（时间线事件，进日志）。"""
    return {
        "type": ENTRY_TYPE_SYSTEM,
        "subtype": ENTRY_SUBTYPE_INTERRUPT,
        "uuid": uuid or f"interrupt-{uuid4().hex}",
        "timestamp": timestamp or utc_now_iso(),
    }


def is_interrupt_entry(entry: dict[str, Any]) -> bool:
    return entry.get("type") == ENTRY_TYPE_SYSTEM and entry.get("subtype") == ENTRY_SUBTYPE_INTERRUPT


def _blocks_text(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text")


def _is_interrupt_echo(blocks: list[dict[str, Any]]) -> bool:
    """整条内容即单个中断回显文本块时才判定，正文中途出现同字样不误判。"""
    if len(blocks) != 1 or blocks[0].get("type") != "text":
        return False
    return str(blocks[0].get("text") or "").strip().startswith(_INTERRUPT_ECHO_PREFIX)


def _extract_task_notifications(blocks: list[dict[str, Any]]) -> list[dict[str, str]]:
    """提取消息文本中的全部 task-notification XML（同一 tick 可能批了多条）。"""
    text = _blocks_text(blocks)

    def _tag(xml: str, name: str) -> str:
        m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.DOTALL)
        return m.group(1).strip() if m else ""

    return [
        {
            "task_id": _tag(match.group(0), "task-id"),
            "tool_use_id": _tag(match.group(0), "tool-use-id"),
            "status": _tag(match.group(0), "status"),
            "summary": _tag(match.group(0), "summary"),
        }
        for match in _TASK_NOTIFICATION_RE.finditer(text)
    ]


def _extract_structured_answers(message: dict[str, Any]) -> dict[str, str] | None:
    """从消息级 tool_use_result（CLI 的结构化工具结果）提取答案映射。"""
    tool_use_result = message.get("tool_use_result")
    if not isinstance(tool_use_result, dict):
        return None
    answers = tool_use_result.get("answers")
    if not isinstance(answers, dict) or not answers:
        return None
    return {str(k): str(v) for k, v in answers.items()}


def build_user_entry(
    content_blocks: list[dict[str, Any]],
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """构造用户消息受理时的权威条目（POST 先写日志分配身份再回显）。"""
    return {
        "type": ENTRY_TYPE_USER,
        "content": normalize_content(content_blocks),
        "uuid": f"user-{uuid4().hex}",
        "timestamp": timestamp or utc_now_iso(),
    }


class SdkMessageNormalizer:
    """写入点定型器：把 SDK 消息 dict 规范化为零或多个日志条目。

    - assistant → 单条 assistant 条目（携带 message_id，供 draft 精确替换）；
      AskUserQuestion 的 tool_use_id 登记进实例状态，供答复定型；Skill
      tool_use 登记进实例状态，供随后到达的注入消息定型为 skill_invocation
      系统条目
    - user → interrupt 回显定型为 system/interrupt 条目；task 通知 XML
      （同一 tick 可能批多条）定型为 system/task_notification 条目；
      AskUserQuestion 的 tool_result 定型为 user/question_answer 条目
      （结构化答案取自消息级 tool_use_result）；skill 注入文本定型为
      skill_invocation 系统条目（只记 skill 名与入参，注入全文不进日志）；
      其余 tool_result 块定型为独立条目（引用 tool_use_id）；剩余内容以
      通用 user 条目收录；SDK 回放副本（已打标）不入日志
    - system(task_*) → typed system 条目
    - stream_event / result / 其它 → 不进日志

    实例维护跨消息关联状态：Skill tool_use → 随后到达的注入用户消息；
    AskUserQuestion tool_use → 随后到达的答复 tool_result。Skill 状态按
    归属上下文（parent_tool_use_id，主线为 None）分槽——live 流中主线与各
    subagent 的消息交错到达，各自独立关联。live 管道与懒生成重放共用本类，
    保证两条路径产出相同的 typed 条目。
    """

    def __init__(self) -> None:
        # 上下文 → 未被注入消息消费的 Skill tool_use 元数据队列（FIFO）。
        # 同一上下文可能并发发起多个 Skill 调用（同一 assistant 消息内多个
        # tool_use 块），按调用顺序逐一消费，避免后一个覆盖前一个。
        self._pending_skills: dict[str | None, list[dict[str, Any]]] = {}
        # 已登记但尚未收到答复的 AskUserQuestion tool_use_id。
        self.question_tool_use_ids: set[str] = set()

    def normalize(self, message: Any) -> list[dict[str, Any]]:
        if not isinstance(message, dict):
            return []
        msg_type = message.get("type")

        if msg_type == "assistant":
            content = normalize_content(message.get("content", []))
            for block in content:
                if (
                    block.get("type") == "tool_use"
                    and str(block.get("name") or "").strip().lower() == _ASK_USER_QUESTION_TOOL
                    and block.get("id")
                ):
                    self.question_tool_use_ids.add(str(block["id"]))
            entry: dict[str, Any] = {
                "type": ENTRY_TYPE_ASSISTANT,
                "content": content,
                "message_id": message.get("message_id"),
                "uuid": message.get("uuid") or f"entry-{uuid4().hex}",
                "timestamp": message.get("timestamp") or utc_now_iso(),
            }
            _copy_parent(message, entry)
            self._track_skill_tool_use(message, entry)
            return [entry]

        if msg_type == "user":
            return self._normalize_user(message)

        if msg_type == "system" and message.get("subtype") in _TASK_SUBTYPES:
            sys_entry: dict[str, Any] = {
                "type": ENTRY_TYPE_SYSTEM,
                "subtype": message.get("subtype"),
                "task_id": message.get("task_id"),
                "description": message.get("description", ""),
                "summary": message.get("summary"),
                "task_status": message.get("status"),
                "usage": message.get("usage"),
                "tool_use_id": message.get("tool_use_id"),
                "uuid": message.get("uuid") or f"entry-{uuid4().hex}",
                "timestamp": message.get("timestamp") or utc_now_iso(),
            }
            _copy_parent(message, sys_entry)
            return [sys_entry]

        return []

    def _normalize_user(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        if message.get(REPLAYED_USER_ECHO_KEY):
            return []
        blocks = normalize_content(message.get("content", ""))
        tool_results = [b for b in blocks if b.get("type") == "tool_result"]
        timestamp = message.get("timestamp") or utc_now_iso()
        base_uuid = message.get("uuid")
        parent = _extract_parent(message)

        if not tool_results:
            if _is_interrupt_echo(blocks):
                return [
                    build_interrupt_entry(
                        uuid=base_uuid,
                        timestamp=message.get("timestamp"),
                    )
                ]
            task_infos = _extract_task_notifications(blocks)
            if task_infos:
                # 单条通知（绝大多数场景）保留原 uuid 不变；同一消息批了多条时才
                # 加序号后缀区分，避免同 uuid 的多条系统条目在前端归并/查找时互相覆盖。
                multiple = len(task_infos) > 1
                return [
                    {
                        "type": ENTRY_TYPE_SYSTEM,
                        "subtype": ENTRY_SUBTYPE_TASK_NOTIFICATION,
                        "task_id": task_info["task_id"] or None,
                        "description": "",
                        "summary": task_info["summary"] or None,
                        "task_status": task_info["status"] or None,
                        "tool_use_id": task_info["tool_use_id"] or None,
                        "uuid": (
                            f"{base_uuid}-tn{i}" if multiple and base_uuid else (base_uuid or f"entry-{uuid4().hex}")
                        ),
                        "timestamp": timestamp,
                    }
                    for i, task_info in enumerate(task_infos)
                ]

        # tool_use_result 是消息级字段（CLI 单条 toolUseResult，非按 tool_use_id
        # 分片）：仅当本消息只批了一个待答复的 tool_result 时，才能确定该结构化
        # 答案属于它；同一消息里批了多个提问的 tool_result 时无法判定各自归属，
        # 宁可回退到原始文本也不要把同一份答案错配给多个问题。
        question_result_count = sum(
            1
            for block in tool_results
            if block.get("tool_use_id") and str(block["tool_use_id"]) in self.question_tool_use_ids
        )

        entries: list[dict[str, Any]] = []
        others: list[dict[str, Any]] = []
        tool_result_index = 0
        skill_index = 0
        for block in blocks:
            if block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if tool_use_id and str(tool_use_id) in self.question_tool_use_ids:
                    qa_entry: dict[str, Any] = {
                        "type": ENTRY_TYPE_USER,
                        "subtype": ENTRY_SUBTYPE_QUESTION_ANSWER,
                        "tool_use_id": tool_use_id,
                        "content": _stringify_content(block.get("content", "")),
                        "is_error": bool(block.get("is_error", False)),
                        "answers": _extract_structured_answers(message) if question_result_count == 1 else None,
                        "uuid": f"{base_uuid}-tr{tool_result_index}" if base_uuid else f"entry-{uuid4().hex}",
                        "timestamp": timestamp,
                    }
                    _copy_parent(message, qa_entry)
                    entries.append(qa_entry)
                    tool_result_index += 1
                    continue
                tr_entry: dict[str, Any] = {
                    "type": ENTRY_TYPE_TOOL_RESULT,
                    "tool_use_id": tool_use_id,
                    "content": block.get("content", ""),
                    "is_error": bool(block.get("is_error", False)),
                    "uuid": f"{base_uuid}-tr{tool_result_index}" if base_uuid else f"entry-{uuid4().hex}",
                    "timestamp": timestamp,
                }
                _copy_parent(message, tr_entry)
                entries.append(tr_entry)
                tool_result_index += 1
                continue
            text = block.get("text") if block.get("type") == "text" else None
            if isinstance(text, str) and _is_skill_injection_text(text.strip()):
                entries.append(self._build_skill_entry(text.strip(), timestamp, base_uuid, skill_index, parent))
                skill_index += 1
                continue
            others.append(block)
        if others:
            user_entry: dict[str, Any] = {
                "type": ENTRY_TYPE_USER,
                "content": others,
                "uuid": base_uuid or f"entry-{uuid4().hex}",
                "timestamp": timestamp,
            }
            _copy_parent(message, user_entry)
            entries.append(user_entry)
        return entries

    def _build_skill_entry(
        self,
        text: str,
        timestamp: str,
        base_uuid: Any,
        index: int,
        parent: str | None,
    ) -> dict[str, Any]:
        queue = self._pending_skills.get(parent)
        pending = queue.pop(0) if queue else {}
        if queue is not None and not queue:
            self._pending_skills.pop(parent, None)
        entry: dict[str, Any] = {
            "type": ENTRY_TYPE_SYSTEM,
            "subtype": SYSTEM_SUBTYPE_SKILL_INVOCATION,
            "skill_name": pending.get("name") or _parse_skill_name_from_injection(text),
            "skill_args": pending.get("args"),
            "tool_use_id": pending.get("tool_use_id"),
            "uuid": f"{base_uuid}-skill{index}" if base_uuid else f"entry-{uuid4().hex}",
            "timestamp": timestamp,
        }
        if parent:
            entry["parent_tool_use_id"] = parent
        return entry

    def _track_skill_tool_use(self, message: dict[str, Any], entry: dict[str, Any]) -> None:
        parent = _extract_parent(message)
        for block in entry.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Skill":
                continue
            raw_input = block.get("input")
            input_data: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
            name = input_data.get("skill") or input_data.get("name")
            args = input_data.get("args")
            self._pending_skills.setdefault(parent, []).append(
                {
                    "tool_use_id": block.get("id"),
                    "name": name if isinstance(name, str) and name else None,
                    "args": args if isinstance(args, str) and args else None,
                }
            )


def normalize_sdk_message_to_entries(message: Any) -> list[dict[str, Any]]:
    """一次性定型单条消息（skill 注入/AskUserQuestion 的跨消息关联需持有
    SdkMessageNormalizer 实例）。"""
    return SdkMessageNormalizer().normalize(message)


def _assistant_tool_use_ids(message: Any) -> list[str]:
    """assistant 消息内 tool_use 块的 id 列表（subagent 子时间线的锚定候选）。"""
    if not isinstance(message, dict) or message.get("type") != "assistant":
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    ids: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            block_id = block.get("id")
            if isinstance(block_id, str) and block_id:
                ids.append(block_id)
    return ids


class EventLogStore:
    """事件日志 DB 访问：seq 单调分配（append-only）+ 幂等键查重。"""

    def __init__(self, *, session_factory=None, user_id: str = DEFAULT_USER_ID):
        self._session_factory = session_factory or safe_session_factory
        self._user_id = user_id

    async def append(
        self,
        session_id: str,
        entries: list[dict[str, Any]],
        *,
        client_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """追加条目并返回带 seq 的权威条目列表。

        seq 分配采用 max+1 + 主键冲突重试（与 DbSessionStore.append 同模式）；
        ``client_key`` 只对单条 user 条目有意义，唯一索引兜底并发重试。
        """
        if not entries:
            return []
        for attempt in range(_MAX_APPEND_RETRY):
            try:
                return await self._append_once(session_id, entries, client_key)
            except IntegrityError as exc:
                msg = str(exc.orig) if exc.orig else str(exc)
                unique_violation = "UNIQUE" in msg or "duplicate key" in msg
                if unique_violation and "client_key" in msg and client_key is not None:
                    # 幂等键并发冲突：另一请求已写入同键条目，返回其权威条目。
                    existing = await self.find_by_client_key(session_id, client_key)
                    if existing is not None:
                        return [existing]
                    raise
                # 表上只有两个唯一约束（(session_id, seq) 主键 + client_key 唯一索引）；
                # 排除 client_key 冲突即可确定是 seq 竞争，不依赖驱动/配置相关的
                # 错误信息措辞（不同驱动对主键冲突的 DETAIL 文案不一定含 "seq"）。
                is_seq_race = unique_violation and "client_key" not in msg
                if not is_seq_race or attempt == _MAX_APPEND_RETRY - 1:
                    raise
                delay = random.uniform(0, min(_APPEND_BACKOFF_CAP_S, 0.001 * (2**attempt)))
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _append_once(
        self,
        session_id: str,
        entries: list[dict[str, Any]],
        client_key: str | None,
    ) -> list[dict[str, Any]]:
        now_dt = utc_now()
        async with self._session_factory() as session:
            seq_start_row = await session.execute(
                select(func.coalesce(func.max(AgentSessionEventLogEntry.seq), -1) + 1).where(
                    AgentSessionEventLogEntry.session_id == session_id,
                )
            )
            seq_start = int(seq_start_row.scalar_one())
            enriched: list[dict[str, Any]] = []
            for i, entry in enumerate(entries):
                seq = seq_start + i
                session.add(
                    AgentSessionEventLogEntry(
                        session_id=session_id,
                        seq=seq,
                        entry_type=str(entry.get("type") or ""),
                        payload=entry,
                        client_key=client_key if len(entries) == 1 else None,
                        user_id=self._user_id,
                        created_at=now_dt,
                        updated_at=now_dt,
                    )
                )
                enriched.append({"seq": seq, **entry})
            await session.commit()
        return enriched

    async def append_user_entry(
        self,
        session_id: str,
        entry: dict[str, Any],
        *,
        client_key: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """幂等追加用户条目：同一幂等键重试返回既有条目，不产生重复。

        Returns:
            (权威条目, 是否新写入)
        """
        if client_key:
            existing = await self.find_by_client_key(session_id, client_key)
            if existing is not None:
                return existing, False
        appended = await self.append(session_id, [entry], client_key=client_key)
        authoritative = appended[0]
        # append 在幂等键冲突时返回既有条目；用 uuid 区分是否为本次写入。
        created = authoritative.get("uuid") == entry.get("uuid")
        return authoritative, created

    async def find_by_client_key(self, session_id: str, client_key: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentSessionEventLogEntry.seq, AgentSessionEventLogEntry.payload).where(
                    AgentSessionEventLogEntry.session_id == session_id,
                    AgentSessionEventLogEntry.client_key == client_key,
                )
            )
            row = result.first()
        if row is None:
            return None
        return {"seq": int(row.seq), **row.payload}

    async def list_after(self, session_id: str, after_seq: int = -1) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentSessionEventLogEntry.seq, AgentSessionEventLogEntry.payload)
                .where(
                    AgentSessionEventLogEntry.session_id == session_id,
                    AgentSessionEventLogEntry.seq > after_seq,
                )
                .order_by(AgentSessionEventLogEntry.seq)
            )
            rows = result.all()
        return [{"seq": int(row.seq), **row.payload} for row in rows]

    async def delete_entry(self, session_id: str, seq: int) -> None:
        """补偿删除单条条目（仅限受理失败回滚：SDK 投递失败时撤销刚写入的
        用户条目，否则同幂等键重试会短路而永不投递）。"""
        async with self._session_factory() as session:
            await session.execute(
                sa_delete(AgentSessionEventLogEntry).where(
                    AgentSessionEventLogEntry.session_id == session_id,
                    AgentSessionEventLogEntry.seq == seq,
                )
            )
            await session.commit()

    async def delete_session(self, session_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                sa_delete(AgentSessionEventLogEntry).where(AgentSessionEventLogEntry.session_id == session_id)
            )
            await session.commit()

    async def has_entries(self, session_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(select(exists().where(AgentSessionEventLogEntry.session_id == session_id)))
            return bool(result.scalar())

    async def last_entry(self, session_id: str) -> dict[str, Any] | None:
        """尾条条目（interrupt 相邻去重的写入点检查用）。"""
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentSessionEventLogEntry.seq, AgentSessionEventLogEntry.payload)
                .where(AgentSessionEventLogEntry.session_id == session_id)
                .order_by(AgentSessionEventLogEntry.seq.desc())
                .limit(1)
            )
            row = result.first()
        if row is None:
            return None
        return {"seq": int(row.seq), **row.payload}


class TranscriptReader(Protocol):
    """懒生成读取 transcript 的最小接口（SdkTranscriptAdapter 满足）。"""

    async def read_raw_messages(
        self,
        sdk_session_id: str | None,
        project_cwd: Path | str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def read_subagent_timelines(
        self,
        sdk_session_id: str | None,
        project_cwd: Path | str | None = None,
    ) -> dict[str, list[dict[str, Any]]]: ...


class EventLogService:
    """事件日志读取入口：懒生成 + 游标列举。"""

    def __init__(self, store: EventLogStore, transcript_adapter: TranscriptReader):
        self._store = store
        self._adapter = transcript_adapter
        # 弱引用：无协程持有/等待时锁对象自动回收，避免空会话锁永久残留内存
        self._backfill_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    @property
    def store(self) -> EventLogStore:
        return self._store

    async def ensure_backfilled(self, session_id: str, project_cwd: Path | str | None) -> None:
        """仅有 transcript 的旧会话首次访问时重放重建日志。

        合并 transcript 的 subagent subpath：子时间线条目带 parent_tool_use_id
        标记，插在携带对应 Task tool_use 的主线条目之后，子组内部按自身顺序，
        无需全局排序。
        """
        if await self._store.has_entries(session_id):
            return
        lock = self._backfill_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            if await self._store.has_entries(session_id):
                return
            raw_messages = await self._adapter.read_raw_messages(session_id, project_cwd)
            try:
                subagent_groups = await self._adapter.read_subagent_timelines(session_id, project_cwd)
            except Exception:
                logger.exception("subagent 子时间线读取失败，跳过合并 session_id=%s", session_id)
                subagent_groups = {}
            normalizer = SdkMessageNormalizer()
            entries: list[dict[str, Any]] = []

            def _consume(message: dict[str, Any], parent_tool_use_id: str | None = None) -> None:
                if parent_tool_use_id:
                    message = {**message, "parent_tool_use_id": parent_tool_use_id}
                try:
                    for entry in normalizer.normalize(message):
                        # 相邻 interrupt 回显（SDK 回显 + 竞态副本）收敛为一条，
                        # 与 live 写入点的尾检去重语义一致。
                        if is_interrupt_entry(entry) and entries and is_interrupt_entry(entries[-1]):
                            continue
                        entries.append(entry)
                except Exception:
                    # 容错：历史 transcript 跨版本演进，单条脏数据不应让整个旧
                    # 会话的历史记录懒生成失败。
                    logger.exception("历史消息规范化失败，跳过该条 session_id=%s", session_id)

            for message in raw_messages:
                _consume(message)
                for tool_use_id in _assistant_tool_use_ids(message):
                    for sub_message in subagent_groups.pop(tool_use_id, []):
                        _consume(sub_message, tool_use_id)
            # 主线缺失锚点 tool_use 的残余组仍全量入日志：前端按 parent
            # 归组，无锚时呈现为独立卡片，不丢子时间线数据。
            for tool_use_id, group in subagent_groups.items():
                for sub_message in group:
                    _consume(sub_message, tool_use_id)
            if entries:
                await self._store.append(session_id, entries)
                # 仅在写入成功后清锁引用：此后 has_entries 恒真，旧锁等待者与
                # 新造锁的后来者都会在二次检查处短路。空 transcript 时保留锁，
                # 否则后来者 setdefault 出新锁，与旧锁等待者并发重放、重复灌入。
                self._backfill_locks.pop(session_id, None)

    async def list_entries(
        self,
        session_id: str,
        project_cwd: Path | str | None,
        *,
        after_seq: int = -1,
    ) -> list[dict[str, Any]]:
        await self.ensure_backfilled(session_id, project_cwd)
        return await self._store.list_after(session_id, after_seq)
