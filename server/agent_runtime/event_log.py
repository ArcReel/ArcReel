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
from server.agent_runtime.turn_schema import normalize_content

logger = logging.getLogger(__name__)

ENTRY_TYPE_USER = "user"
ENTRY_TYPE_ASSISTANT = "assistant"
ENTRY_TYPE_TOOL_RESULT = "tool_result"
ENTRY_TYPE_SYSTEM = "system"

_TASK_SUBTYPES = {"task_started", "task_progress", "task_notification"}

# 与 DbSessionStore.append 相同的 seq 竞争重试参数。
_MAX_APPEND_RETRY = 16
_APPEND_BACKOFF_CAP_S = 0.05

# 标记 SDK 回放的用户消息副本（POST 受理时已写日志），供写入点跳过。
# 只存活在进程内消息 dict 上，不落任何持久化层。
REPLAYED_USER_ECHO_KEY = "_replayed_user_echo"


def _copy_parent(message: dict[str, Any], entry: dict[str, Any]) -> None:
    parent = message.get("parent_tool_use_id")
    if isinstance(parent, str) and parent.strip():
        entry["parent_tool_use_id"] = parent


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


def normalize_sdk_message_to_entries(message: Any) -> list[dict[str, Any]]:
    """写入点定型：把一条 SDK 消息 dict 规范化为零或多个日志条目。

    - assistant → 单条 assistant 条目（携带 message_id，供 draft 精确替换）
    - user → tool_result 块定型为独立条目（引用 tool_use_id）；其余内容
      （含过渡期的 interrupt 回显、task 通知 XML、skill 注入、subagent 消息）
      以通用 user 条目收录；local_echo 与 SDK 回放副本不入日志
    - system(task_*) → 通用 system 条目（专属定型由后续片承接）
    - stream_event / result / 其它 → 不进日志
    """
    if not isinstance(message, dict):
        return []
    msg_type = message.get("type")

    if msg_type == "assistant":
        entry: dict[str, Any] = {
            "type": ENTRY_TYPE_ASSISTANT,
            "content": normalize_content(message.get("content", [])),
            "message_id": message.get("message_id"),
            "uuid": message.get("uuid") or f"entry-{uuid4().hex}",
            "timestamp": message.get("timestamp") or utc_now_iso(),
        }
        _copy_parent(message, entry)
        return [entry]

    if msg_type == "user":
        if message.get("local_echo") or message.get(REPLAYED_USER_ECHO_KEY):
            return []
        blocks = normalize_content(message.get("content", ""))
        tool_results = [b for b in blocks if b.get("type") == "tool_result"]
        others = [b for b in blocks if b.get("type") != "tool_result"]
        timestamp = message.get("timestamp") or utc_now_iso()
        base_uuid = message.get("uuid")
        entries: list[dict[str, Any]] = []
        for i, block in enumerate(tool_results):
            tr_entry: dict[str, Any] = {
                "type": ENTRY_TYPE_TOOL_RESULT,
                "tool_use_id": block.get("tool_use_id"),
                "content": block.get("content", ""),
                "is_error": bool(block.get("is_error", False)),
                "uuid": f"{base_uuid}-tr{i}" if base_uuid else f"entry-{uuid4().hex}",
                "timestamp": timestamp,
            }
            _copy_parent(message, tr_entry)
            entries.append(tr_entry)
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


class TranscriptReader(Protocol):
    """懒生成读取 transcript 的最小接口（SdkTranscriptAdapter 满足）。"""

    async def read_raw_messages(
        self,
        sdk_session_id: str | None,
        project_cwd: Path | str | None = None,
    ) -> list[dict[str, Any]]: ...


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
        """仅有 transcript 的旧会话首次访问时重放重建日志（主时间线）。

        subagent subpath 的归组重建由后续片承接；此处只回放主时间线。
        """
        if await self._store.has_entries(session_id):
            return
        lock = self._backfill_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            if await self._store.has_entries(session_id):
                return
            raw_messages = await self._adapter.read_raw_messages(session_id, project_cwd)
            entries: list[dict[str, Any]] = []
            for message in raw_messages:
                try:
                    entries.extend(normalize_sdk_message_to_entries(message))
                except Exception:
                    # 容错：历史 transcript 跨版本演进，单条脏数据不应让整个旧
                    # 会话的历史记录懒生成失败。
                    logger.exception("历史消息规范化失败，跳过该条 session_id=%s", session_id)
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
