"""
Assistant service orchestration using ClaudeSDKClient.
"""

import asyncio
import copy
import logging
import os
import weakref
from collections import OrderedDict
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    delete_session as sdk_delete_session,
)
from claude_agent_sdk import (
    delete_session_via_store,
    list_sessions_from_store,
)
from claude_agent_sdk import (
    list_sessions as sdk_list_sessions,
)

if TYPE_CHECKING:
    from server.routers.assistant import ImageAttachment

logger = logging.getLogger(__name__)

from fastapi import Request
from fastapi.sse import ServerSentEvent

from lib.agent_profile import agent_profile_dir
from lib.app_data_dir import app_data_dir
from lib.i18n import DEFAULT_LOCALE, get_locale
from lib.profile_manifest import VALID_CONTENT_MODES
from lib.project_manager import ProjectManager
from server.agent_runtime.event_log import EventLogService, EventLogStore, build_user_entry
from server.agent_runtime.models import Heartbeat, LiveMessage, SessionMeta, SessionStatus, SubscriptionReady
from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore


class AssistantService:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.projects_root = app_data_dir()
        self.data_dir = self.projects_root / ".agent_data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.pm = ProjectManager(self.projects_root)
        self.meta_store = SessionMetaStore()
        # 会话事件日志：UI 时间线唯一读源。store 与 SessionManager 共享同一实例，
        # live 写入点（entry pipeline）与读取端（REST / SSE / 懒生成）落同一张表。
        self.event_log_store = EventLogStore()
        self.session_manager = SessionManager(
            project_root=self.project_root,
            data_dir=self.data_dir,
            meta_store=self.meta_store,
            projects_root=self.projects_root,
            event_log_store=self.event_log_store,
        )
        # Shared with SessionManager (lazy-cached there) so reads via the
        # adapter and writes via SDK options use the same per-user namespace.
        # None when ARCREEL_SDK_SESSION_STORE=off.
        self._session_store = self.session_manager._build_session_store()
        self.transcript_adapter = SdkTranscriptAdapter(store=self._session_store)
        self.event_log = EventLogService(self.event_log_store, self.transcript_adapter)
        self._startup_lock = asyncio.Lock()
        self._startup_done = False
        # 新会话幂等映射：client_key 唯一索引按 (session_id, client_key) 分区，
        # 覆盖不到 session_id 尚不存在的新会话受理——响应丢失后的重试若再走
        # 新会话分支会重复建会话、重复执行同一 prompt。进程内 LRU 兜底。
        self._new_session_client_keys: OrderedDict[str, str] = OrderedDict()
        self._new_session_client_keys_max = 256
        # 同一 client_key 的并发新建请求在此串行化，避免在途窗口内重复建会话；
        # 无协程持有/等待时锁对象自动回收
        self._new_session_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self.stream_heartbeat_seconds = int(os.environ.get("ASSISTANT_STREAM_HEARTBEAT_SECONDS", "20"))

    async def startup(self, *, in_docker: bool = False, sandbox_enabled: bool = True) -> None:
        """Run async initialization (must be called from event loop).

        ``sandbox_enabled=False`` 时关闭 SDK SandboxSettings 并把 Bash 工具调用
        切到代码白名单路径（详见 ``SessionManager.configure_sandbox_runtime``）。
        默认 ``True`` 保持 macOS / Linux 现状不变。
        """
        if self._startup_done:
            return
        async with self._startup_lock:
            if self._startup_done:
                return
            self.session_manager.configure_sandbox_runtime(
                in_docker=bool(in_docker),
                sandbox_enabled=bool(sandbox_enabled),
            )
            await self._interrupt_stale_running_sessions()
            self._startup_done = True

    # ==================== Session CRUD ====================

    async def _interrupt_stale_running_sessions(self) -> None:
        """On service restart, stale running sessions cannot safely resume."""
        interrupted_count = await self.meta_store.interrupt_running_sessions()
        if interrupted_count > 0:
            logger.warning(
                "服务启动时中断遗留运行中会话 count=%s",
                interrupted_count,
            )

    async def list_sessions(
        self,
        project_name: str | None = None,
        status: SessionStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionMeta]:
        """List sessions, injecting SDK summary as title when available."""
        sessions = await self.meta_store.list(project_name=project_name, status=status, limit=limit, offset=offset)
        if not sessions or not project_name:
            return sessions

        project_cwd = str(self.projects_root / project_name)
        sdk_sessions: list[Any] = []

        if self._session_store is not None and list_sessions_from_store is not None:
            try:
                sdk_sessions = await list_sessions_from_store(self._session_store, directory=project_cwd)  # type: ignore[arg-type]
            except Exception:
                logger.warning(
                    "SDK list_sessions_from_store failed, titles will be empty",
                    exc_info=True,
                )
                return sessions
        elif sdk_list_sessions is not None:
            try:
                sdk_sessions = await asyncio.to_thread(
                    sdk_list_sessions, directory=project_cwd, include_worktrees=False
                )
            except Exception:
                logger.warning("SDK list_sessions failed, titles will be empty", exc_info=True)
                return sessions
        else:
            return sessions

        summary_map = {s.session_id: s.summary for s in sdk_sessions}
        return [SessionMeta(**{**s.model_dump(), "title": summary_map.get(s.id, s.title)}) for s in sessions]

    async def get_session(self, session_id: str) -> SessionMeta | None:
        """Get session by ID."""
        meta = await self.meta_store.get(session_id)
        if meta and session_id in self.session_manager.sessions:
            # Update status from live session
            managed = self.session_manager.sessions[session_id]
            meta = SessionMeta(**{**meta.model_dump(), "status": managed.status})
        return meta

    async def delete_session(self, session_id: str) -> bool:
        """Delete session and cleanup."""
        if session_id in self.session_manager.sessions:
            await self.session_manager.close_session(
                session_id,
                reason="session deleted",
            )

        if self._session_store is not None and delete_session_via_store is not None:
            # SDK derives project_key from `directory`; without it the key is
            # computed from server cwd and never matches inserted rows, so the
            # delete becomes a silent no-op. Resolve project cwd from meta.
            meta = await self.meta_store.get(session_id)
            project_cwd = str(self.projects_root / meta.project_name) if meta else None
            try:
                await delete_session_via_store(self._session_store, session_id, directory=project_cwd)  # type: ignore[arg-type]
            except Exception:
                logger.warning(
                    "delete_session_via_store failed for %s",
                    session_id,
                    exc_info=True,
                )
        elif sdk_delete_session is not None:
            try:
                await asyncio.to_thread(sdk_delete_session, session_id)
            except Exception:
                logger.warning("sdk delete_session failed for %s", session_id, exc_info=True)

        try:
            await self.event_log_store.delete_session(session_id)
        except Exception:
            logger.warning("删除会话事件日志失败 session_id=%s", session_id, exc_info=True)

        return await self.meta_store.delete(session_id)

    # ==================== Messages ====================

    def _prepare_prompt(
        self,
        content: str,
        images: list["ImageAttachment"] | None = None,
    ) -> tuple[str, Any | None, list[dict[str, Any]] | None]:
        """Prepare prompt components: (text, sdk_prompt_or_none, echo_blocks_or_none)."""
        text = content.strip()
        if not text and not images:
            raise ValueError("消息内容不能为空")

        if images:
            sdk_prompt = self._build_multimodal_prompt(text, images)
            echo_blocks: list[dict[str, Any]] = [self._image_block(img) for img in images]
            if text:
                echo_blocks.append({"type": "text", "text": text})
            return text, sdk_prompt, echo_blocks
        return text, None, None

    @staticmethod
    def _build_user_log_entry(text: str, echo_blocks: list[dict[str, Any]] | None) -> dict[str, Any]:
        """构造用户消息的受理条目（写入点定型；POST 先写日志分配身份再回显）。"""
        blocks = echo_blocks if echo_blocks is not None else [{"type": "text", "text": text}]
        return build_user_entry(blocks)

    async def send_or_create(
        self,
        project_name: str,
        content: str,
        *,
        session_id: str | None = None,
        images: list["ImageAttachment"] | None = None,
        locale: str = DEFAULT_LOCALE,
        client_key: str | None = None,
    ) -> dict[str, Any]:
        """Unified send: create new session or send to existing one.

        响应携带权威日志条目（``entry``）：服务端先写日志分配身份，前端
        直接以该条目回显，不渲染任何本地合成消息；``client_key`` 为请求侧
        幂等键，重试不产生重复条目。
        """
        self.pm.get_project_path(project_name)  # Validate project

        if session_id:
            # Existing session
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")
            if meta.project_name != project_name:
                raise FileNotFoundError(f"session not found: {session_id}")
            # Build prompt
            text, sdk_prompt, echo_blocks = self._prepare_prompt(content, images)
            # 旧会话懒生成先行：保证受理条目排在重放重建的历史之后。
            await self.event_log.ensure_backfilled(session_id, self._resolve_project_cwd_safe(meta.project_name))
            user_entry = self._build_user_log_entry(text, echo_blocks)
            entry = await self.session_manager.send_message(
                session_id,
                sdk_prompt if sdk_prompt is not None else text,
                echo_text=text,
                echo_content=echo_blocks,
                meta=meta,
                locale=locale,
                user_entry=user_entry,
                client_key=client_key,
            )
            return {"status": "accepted", "session_id": session_id, "entry": entry}
        else:
            # New session
            if not client_key:
                return await self._create_new_session(project_name, content, images, locale, client_key)

            mapped_session_id = self._new_session_client_keys.get(client_key)
            if mapped_session_id is not None:
                # 幂等重试：首次受理已建会话并投递，返回同一会话的权威条目。
                entry = await self.event_log_store.find_by_client_key(mapped_session_id, client_key)
                return {"status": "accepted", "session_id": mapped_session_id, "entry": entry}

            # 同一 client_key 的并发请求在此串行化：send_new_session 在途期间
            # 后来者等锁而非各自建会话，避免重复执行同一 prompt。
            lock = self._new_session_locks.setdefault(client_key, asyncio.Lock())
            async with lock:
                # 双重检查：等锁期间先行者可能已完成同一 client_key 的建会话。
                mapped_session_id = self._new_session_client_keys.get(client_key)
                if mapped_session_id is not None:
                    entry = await self.event_log_store.find_by_client_key(mapped_session_id, client_key)
                    return {"status": "accepted", "session_id": mapped_session_id, "entry": entry}
                result = await self._create_new_session(project_name, content, images, locale, client_key)
                self._new_session_client_keys[client_key] = result["session_id"]
                while len(self._new_session_client_keys) > self._new_session_client_keys_max:
                    self._new_session_client_keys.popitem(last=False)
                return result

    async def _create_new_session(
        self,
        project_name: str,
        content: str,
        images: list["ImageAttachment"] | None,
        locale: str,
        client_key: str | None,
    ) -> dict[str, Any]:
        """实际创建新会话并投递首条消息，不涉及 client_key 幂等映射记账。"""
        text, sdk_prompt, echo_blocks = self._prepare_prompt(content, images)
        prompt = sdk_prompt if sdk_prompt is not None else text
        user_entry = self._build_user_log_entry(text, echo_blocks)
        new_sdk_session_id = await self.session_manager.send_new_session(
            project_name,
            prompt,
            echo_text=text,
            echo_content=echo_blocks,
            locale=locale,
            user_entry=user_entry,
            client_key=client_key,
        )
        managed = self.session_manager.sessions.get(new_sdk_session_id)
        entry = managed.initial_user_log_entry if managed is not None else None
        return {"status": "accepted", "session_id": new_sdk_session_id, "entry": entry}

    @staticmethod
    def _image_block(img: "ImageAttachment") -> dict[str, Any]:
        """Build a single image content block dict."""
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img.media_type,
                "data": img.data,
            },
        }

    @staticmethod
    def _build_multimodal_prompt(
        text: str,
        images: list["ImageAttachment"],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Build an async generator yielding a single multimodal user message for Claude SDK.

        The SDK's query() method writes each item from the AsyncIterable directly to the
        transport as a wire protocol message. So we must yield one complete user message
        dict (with type/message/parent_tool_use_id fields), not individual content blocks.
        """

        async def _gen() -> AsyncGenerator[dict[str, Any], None]:
            content: list[dict[str, Any]] = [AssistantService._image_block(img) for img in images]
            if text:
                content.append({"type": "text", "text": text})
            yield {
                "type": "user",
                "message": {"role": "user", "content": content},
                "parent_tool_use_id": None,
            }

        return _gen()

    async def answer_user_question(
        self,
        session_id: str,
        question_id: str,
        answers: dict[str, str],
        *,
        meta: SessionMeta | None = None,
    ) -> dict[str, Any]:
        """Submit answers for a pending AskUserQuestion."""
        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")
        await self.session_manager.answer_user_question(session_id, question_id, answers)
        return {"status": "accepted", "session_id": session_id, "question_id": question_id}

    async def interrupt_session(self, session_id: str, *, meta: SessionMeta | None = None) -> dict[str, Any]:
        """Interrupt a running session."""
        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")
        session_status = await self.session_manager.interrupt_session(session_id)
        return {
            "status": "accepted",
            "session_id": session_id,
            "session_status": session_status,
        }

    # ==================== 会话事件日志（UI 时间线唯一读源） ====================

    async def list_session_entries(
        self,
        session_id: str,
        *,
        meta: SessionMeta | None = None,
        after_seq: int = -1,
    ) -> dict[str, Any]:
        """冷读事件日志（历史回放 / 非 running 会话初始加载）。"""
        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")
        status = await self.session_manager.get_status(session_id) or meta.status
        project_cwd = self._resolve_project_cwd_safe(meta.project_name)
        entries = await self.event_log.list_entries(session_id, project_cwd, after_seq=after_seq)
        draft_state = (
            self.session_manager.get_draft_state(session_id) if status == "running" else {"draft": None, "rev": 0}
        )
        return {
            "session_id": session_id,
            "status": status,
            "entries": entries,
            "draft": draft_state["draft"],
            "draft_rev": draft_state["rev"],
        }

    async def stream_entry_events(
        self,
        session_id: str,
        *,
        meta: SessionMeta | None = None,
        request: Request | None = None,
        after_seq: int = -1,
    ) -> AsyncIterator[ServerSentEvent]:
        """SSE entry 流：事件 ``id`` 即 seq，断线重连按 cursor 续传、不整帧重算。

        序列协议：``entry``×N（cursor 之后的存量）→ ``draft``（首帧快照携带
        流式累积态 + rev 过滤门槛）→ ``question``×N（未决问题）→ 直播
        （entry / delta / question / status）。非 running 会话产出存量 entry
        与终态 status 后即结束。
        """
        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")

        initial_status = await self.session_manager.get_status(session_id) or meta.status
        project_cwd = self._resolve_project_cwd_safe(meta.project_name)

        if initial_status != "running":
            for entry in await self.event_log.list_entries(session_id, project_cwd, after_seq=after_seq):
                yield self._entry_sse_event(entry)
            yield self._sse_event(
                "status",
                self._build_status_event_payload(status=initial_status, session_id=session_id),
            )
            return

        locale = get_locale(request) if request is not None else DEFAULT_LOCALE
        last_seq = after_seq
        async with self.session_manager.stream_messages(
            session_id, idle_timeout=self.stream_heartbeat_seconds, locale=locale
        ) as stream:
            ready = await anext(stream, None)
            if not isinstance(ready, SubscriptionReady):
                return
            # 订阅已先行建立（无缝隙）；订阅与库读之间重复投递的条目由 seq
            # 门槛过滤——身份比对，非内容比对。
            for entry in await self.event_log.list_entries(session_id, project_cwd, after_seq=last_seq):
                last_seq = max(last_seq, self._entry_seq(entry))
                yield self._entry_sse_event(entry)

            draft_state = self.session_manager.get_draft_state(session_id)
            yield self._sse_event("draft", {"session_id": session_id, **draft_state})

            for question in await self.session_manager.get_pending_questions_snapshot(session_id):
                yield self._sse_event("question", {**question, "session_id": session_id})

            status: SessionStatus = await self.session_manager.get_status(session_id) or initial_status
            if status != "running":
                yield self._sse_event(
                    "status",
                    self._build_status_event_payload(status=status, session_id=session_id),
                )
                return

            # 原始 result 由 actor 回调同步广播，而末条 log_entry 由 inbox 任务
            # 落库后才广播——在 result 处直接终结会丢末条条目。改为暂存 result，
            # 等 inbox 串行序上的 log_turn_complete（此时本轮条目已全部广播）
            # 再产出终态；心跳兜底防 inbox 停摆时悬挂。
            pending_result: dict[str, Any] | None = None
            drain_beats = 0
            async for stream_event in stream:
                if request is not None and await request.is_disconnected():
                    break

                if isinstance(stream_event, Heartbeat):
                    if pending_result is not None:
                        drain_beats += 1
                        if drain_beats >= 2:
                            yield self._result_status_event(pending_result, session_id)
                            break
                        continue
                    live_status = await self.session_manager.get_status(session_id) or status
                    if live_status != "running":
                        yield self._sse_event(
                            "status",
                            self._build_status_event_payload(status=live_status, session_id=session_id),
                        )
                        break
                    continue

                if not isinstance(stream_event, LiveMessage):
                    continue

                message = stream_event.message
                msg_type = message.get("type", "")

                if msg_type == "log_entry":
                    entry = message.get("entry")
                    if isinstance(entry, dict):
                        seq = self._entry_seq(entry)
                        if seq > last_seq:
                            last_seq = seq
                            yield self._entry_sse_event(entry)
                    continue

                if msg_type == "log_delta":
                    yield self._sse_event("delta", {k: v for k, v in message.items() if k != "type"})
                    continue

                if msg_type == "log_turn_complete":
                    if pending_result is not None:
                        yield self._result_status_event(pending_result, session_id)
                        break
                    continue

                if msg_type == "ask_user_question":
                    yield self._sse_event(
                        "question",
                        await self._with_session_metadata(copy.deepcopy(message), session_id=session_id),
                    )
                    continue

                if msg_type == "runtime_status":
                    terminal = self._check_runtime_status_terminal(message, session_id)
                    if terminal is not None:
                        yield terminal
                        break
                    continue

                if msg_type == "result":
                    pending_result = message
                    continue

    def _result_status_event(self, result_message: dict[str, Any], session_id: str) -> ServerSentEvent:
        return self._sse_event(
            "status",
            self._build_status_event_payload(
                status=self._resolve_result_status(result_message),
                session_id=session_id,
                result_message=result_message,
            ),
        )

    @staticmethod
    def _entry_seq(entry: dict[str, Any]) -> int:
        seq = entry.get("seq")
        return seq if isinstance(seq, int) else -1

    @staticmethod
    def _entry_sse_event(entry: dict[str, Any]) -> ServerSentEvent:
        """entry 事件：SSE ``id`` 字段即 seq，EventSource 原生 Last-Event-ID 续传。"""
        return ServerSentEvent(event="entry", data=entry, id=str(entry.get("seq")))

    _TERMINAL_STATUSES = {"idle", "running", "completed", "error", "interrupted"}

    def _check_runtime_status_terminal(self, message: dict[str, Any], session_id: str) -> ServerSentEvent | None:
        """Return a status SSE event if *message* carries a terminal runtime status."""
        runtime_status = str(message.get("status") or "").strip()
        if runtime_status in self._TERMINAL_STATUSES:
            return self._sse_event(
                "status",
                self._build_status_event_payload(
                    status=runtime_status,  # type: ignore[arg-type]
                    session_id=session_id,
                    result_message=message,
                ),
            )
        return None

    @staticmethod
    def _sse_event(event: str, data: dict[str, Any]) -> ServerSentEvent:
        """Build an SSE event for FastAPI's EventSourceResponse."""
        return ServerSentEvent(event=event, data=data)

    def _resolve_project_cwd_safe(self, project_name: str) -> Path | None:
        """Resolve the project's working directory, returning None on failure.

        ``SdkTranscriptAdapter`` needs ``project_cwd`` to derive the
        per-project key when reading from the SessionStore. If the project
        directory is missing (deleted, never materialized in tests, etc.)
        we fall back to None — the store helper / SDK defaults handle that.
        """
        try:
            return self.pm.get_project_path(project_name)
        except (FileNotFoundError, ValueError):
            return None

    @staticmethod
    def _resolve_result_status(result_message: dict[str, Any]) -> SessionStatus:
        """Map SDK result subtype/is_error to runtime session status."""
        explicit_status = str(result_message.get("session_status") or "").strip()
        if explicit_status in {"idle", "running", "completed", "error", "interrupted"}:
            return explicit_status  # type: ignore[return-value]
        subtype = str(result_message.get("subtype") or "").strip().lower()
        is_error = bool(result_message.get("is_error"))
        if is_error or subtype.startswith("error"):
            return "error"
        return "completed"

    @staticmethod
    def _build_status_event_payload(
        status: SessionStatus,
        session_id: str,
        result_message: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build normalized status event payload."""
        message = result_message if isinstance(result_message, dict) else {}
        subtype = message.get("subtype")
        stop_reason = message.get("stop_reason")
        is_error = bool(message.get("is_error"))

        if status == "error" and subtype is None:
            subtype = "error"
        if status == "error":
            is_error = True

        payload: dict[str, Any] = {
            "status": status,
            "subtype": subtype,
            "stop_reason": stop_reason,
            "is_error": is_error,
            "session_id": session_id,
        }
        api_error_status = message.get("api_error_status")  # SDK 0.1.76+
        if api_error_status is not None:
            payload["api_error_status"] = api_error_status
        return payload

    async def _with_session_metadata(
        self,
        payload: dict[str, Any],
        *,
        session_id: str,
    ) -> dict[str, Any]:
        """Normalize outward-facing event payloads."""
        normalized = dict(payload)
        normalized["session_id"] = session_id
        normalized.pop("sdk_session_id", None)
        return normalized

    # ==================== Lifecycle ====================

    async def shutdown(self) -> None:
        """Shutdown service gracefully."""
        await self.session_manager.shutdown_gracefully()

    # ==================== Skills ====================

    # Lucide icon hint for each user-invocable skill. The display name is
    # **not** stored here — the frontend resolves it from i18n
    # ``dashboard:skill_name_<id>`` (single source of truth for skill labels
    # lives in ``frontend/src/i18n/{zh,en,vi}/dashboard.ts``).
    # ``tests/test_frontend_skill_i18n.py`` cross-checks SKILL.md against
    # those keys so adding a user-invocable skill without translations fails CI.
    _SKILL_ICONS: dict[str, str] = {
        "manga-workflow": "clapperboard",
        "generate-storyboard": "images",
        "generate-grid": "grid-2x2",
        "generate-video": "film",
        "generate-narration-audio": "audio-lines",
        "generate-assets": "users",
        "compose-video": "scissors",
    }

    def list_available_skills(self, project_name: str | None = None) -> list[dict[str, str]]:
        """List available skills."""
        if project_name:
            self.pm.get_project_path(project_name)

        source_roots = {
            "agent": agent_profile_dir() / ".claude" / "skills",
        }

        skills: list[dict[str, str]] = []
        seen_keys: set[str] = set()

        for scope, root in source_roots.items():
            if not root.exists() or not root.is_dir():
                continue
            try:
                directories = sorted(root.iterdir())
            except OSError:
                continue

            for skill_dir in directories:
                if not skill_dir.is_dir():
                    continue
                skill_file = self._resolve_skill_entry_file(skill_dir)
                if skill_file is None:
                    continue

                try:
                    metadata = self._load_skill_metadata(skill_file, skill_dir.name)
                except OSError:
                    continue

                if not metadata["user_invocable"]:
                    continue

                key = f"{scope}:{metadata['name']}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                skill_entry: dict[str, Any] = {
                    "name": metadata["name"],
                    "description": metadata["description"],
                    "scope": scope,
                    "path": str(skill_file),
                }
                icon = self._SKILL_ICONS.get(metadata["name"])
                if icon:
                    skill_entry["icon"] = icon
                skills.append(skill_entry)

        return skills

    @staticmethod
    def _resolve_skill_entry_file(skill_dir: Path) -> Path | None:
        # profile 端的 content_mode 变体（SKILL.narration.md / SKILL.drama.md）只在 sync
        # 进项目目录时才会被物化为 SKILL.md；列表接口直接扫 profile 时必须自己识别变体，
        # 否则 manga-workflow 这类 variant-only skill 永远拿不到。
        #
        # 查找契约与 tests/test_frontend_skill_i18n.py:_find_skill_md 保持一致：
        # 用 is_file 严格筛文件、按 sorted(VALID_CONTENT_MODES) 显式枚举有效模式、
        # 校验所有变体的 user-invocable 状态一致。不一致时 warning 后返回 None
        # 跳过该 skill——避免列表里随机选到某个 mode 的 frontmatter 导致行为漂移。
        common = skill_dir / "SKILL.md"
        if common.is_file():
            return common
        variants = [skill_dir / f"SKILL.{mode}.md" for mode in sorted(VALID_CONTENT_MODES)]
        existing = [v for v in variants if v.is_file()]
        if not existing:
            return None
        try:
            states = {AssistantService._load_skill_metadata(v, skill_dir.name)["user_invocable"] for v in existing}
        except OSError:
            return None
        if len(states) > 1:
            logger.warning(
                "skill %s 各 content_mode 变体的 user-invocable 不一致，跳过；"
                "请保证所有 SKILL.<mode>.md frontmatter 的 user-invocable 字段相同",
                skill_dir.name,
            )
            return None
        return existing[0]

    @staticmethod
    def _load_skill_metadata(skill_file: Path, fallback_name: str) -> dict[str, Any]:
        """Load skill metadata from SKILL.md frontmatter.

        Parsed fields: name, description, user-invocable.
        """
        content = skill_file.read_text(encoding="utf-8", errors="ignore")
        name = fallback_name
        description = ""
        user_invocable = True

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                body = parts[2]
                for line in frontmatter.splitlines():
                    if ":" not in line:
                        continue
                    key, value = line.split(":", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key == "name" and value:
                        name = value
                    elif key == "description" and value:
                        description = value
                    elif key == "user-invocable":
                        user_invocable = value.lower() not in ("false", "no", "0")
                if not description:
                    for line in body.splitlines():
                        text = line.strip()
                        if text and not text.startswith("#"):
                            description = text
                            break
        else:
            for line in content.splitlines():
                text = line.strip()
                if text and not text.startswith("#"):
                    description = text
                    break

        return {
            "name": name,
            "description": description,
            "user_invocable": user_invocable,
        }
