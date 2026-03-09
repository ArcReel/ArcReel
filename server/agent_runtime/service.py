"""
Assistant service orchestration using ClaudeSDKClient.
"""

import asyncio
import copy
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger(__name__)

from fastapi.sse import ServerSentEvent

from lib.project_manager import ProjectManager
from server.agent_runtime.models import SessionMeta, SessionStatus
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore
from server.agent_runtime.stream_projector import AssistantStreamProjector
from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter
from server.agent_runtime.turn_grouper import (
    _has_subagent_user_metadata,
    _is_system_injected_user_message,
)
from server.agent_runtime.message_utils import extract_plain_user_content
from server.agent_runtime.turn_schema import normalize_turns


class AssistantService:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self._load_project_env(self.project_root)
        self.projects_root = self.project_root / "projects"
        self.data_dir = self.projects_root / ".agent_data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.pm = ProjectManager(self.projects_root)
        self.meta_store = SessionMetaStore()
        self.transcript_adapter = SdkTranscriptAdapter()
        self.session_manager = SessionManager(
            project_root=self.project_root,
            data_dir=self.data_dir,
            meta_store=self.meta_store,
        )
        self._startup_lock = asyncio.Lock()
        self._startup_done = False
        self._snapshot_cache: dict[str, dict[str, Any]] = {}  # session_id → snapshot
        self._snapshot_cache_max = 128
        self.stream_heartbeat_seconds = int(
            os.environ.get("ASSISTANT_STREAM_HEARTBEAT_SECONDS", "20")
        )

    async def startup(self) -> None:
        """Run async initialization (must be called from event loop)."""
        if self._startup_done:
            return
        async with self._startup_lock:
            if self._startup_done:
                return
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

    async def create_session(self, project_name: str, title: str = "") -> SessionMeta:
        """Create a new session."""
        self.pm.get_project_path(project_name)  # Validate project exists
        normalized_title = title.strip() or f"{project_name} 会话"
        session = await self.session_manager.create_session(project_name, normalized_title)
        logger.info("创建会话 session_id=%s project=%s", session.id, project_name)
        return session

    async def list_sessions(
        self,
        project_name: Optional[str] = None,
        status: Optional[SessionStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionMeta]:
        """List sessions."""
        return await self.meta_store.list(
            project_name=project_name, status=status, limit=limit, offset=offset
        )

    async def get_session(self, session_id: str) -> Optional[SessionMeta]:
        """Get session by ID."""
        meta = await self.meta_store.get(session_id)
        if meta and session_id in self.session_manager.sessions:
            # Update status from live session
            managed = self.session_manager.sessions[session_id]
            meta = SessionMeta(
                **{**meta.model_dump(), "status": managed.status}
            )
        return meta

    async def update_session_title(self, session_id: str, title: str) -> Optional[SessionMeta]:
        """Update session title."""
        if await self.meta_store.get(session_id) is None:
            return None
        normalized = title.strip() or "未命名会话"
        if not await self.meta_store.update_title(session_id, normalized):
            return None
        return await self.meta_store.get(session_id)

    async def delete_session(self, session_id: str) -> bool:
        """Delete session and cleanup."""
        # Disconnect if active
        if session_id in self.session_manager.sessions:
            managed = self.session_manager.sessions[session_id]
            managed.cancel_pending_questions("session deleted")
            if managed.consumer_task and not managed.consumer_task.done():
                managed.consumer_task.cancel()
            try:
                await managed.client.disconnect()
            except Exception as exc:
                logger.warning("会话断开清理异常: %s", exc)
            del self.session_manager.sessions[session_id]

        self._snapshot_cache.pop(session_id, None)
        return await self.meta_store.delete(session_id)

    # ==================== Messages ====================

    async def get_snapshot(self, session_id: str, *, meta: Optional[SessionMeta] = None) -> dict[str, Any]:
        """Build a normalized v2 snapshot for history and reconnect."""
        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")

        status = await self.session_manager.get_status(session_id) or meta.status

        # Return cached snapshot for terminal (non-running) sessions
        if status != "running" and session_id in self._snapshot_cache:
            return copy.deepcopy(self._snapshot_cache[session_id])

        projector = await self._build_projector(meta, session_id)

        pending_questions = []
        if status == "running":
            pending_questions = await self.session_manager.get_pending_questions_snapshot(
                session_id
            )
        snapshot = await self._with_session_metadata(
            projector.build_snapshot(
                session_id=session_id,
                status=status,
                pending_questions=pending_questions,
            ),
            session_id=session_id,
        )

        # Cache snapshots for terminal sessions (transcript won't change)
        if status != "running":
            if len(self._snapshot_cache) >= self._snapshot_cache_max:
                # Evict oldest entry (first inserted key in insertion-ordered dict)
                oldest = next(iter(self._snapshot_cache))
                del self._snapshot_cache[oldest]
            self._snapshot_cache[session_id] = snapshot

        return snapshot

    async def send_message(self, session_id: str, content: str, *, meta: Optional[SessionMeta] = None) -> dict[str, Any]:
        """Send a message to the session."""
        text = content.strip()
        if not text:
            raise ValueError("消息内容不能为空")

        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")

        logger.info("发送消息到会话 session_id=%s", session_id)
        self._snapshot_cache.pop(session_id, None)
        await self.session_manager.send_message(session_id, text, meta=meta)
        return {"status": "accepted", "session_id": session_id}

    async def answer_user_question(
        self,
        session_id: str,
        question_id: str,
        answers: dict[str, str],
        *,
        meta: Optional[SessionMeta] = None,
    ) -> dict[str, Any]:
        """Submit answers for a pending AskUserQuestion."""
        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")
        await self.session_manager.answer_user_question(session_id, question_id, answers)
        return {"status": "accepted", "session_id": session_id, "question_id": question_id}

    async def interrupt_session(self, session_id: str, *, meta: Optional[SessionMeta] = None) -> dict[str, Any]:
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

    # ==================== Streaming ====================

    async def stream_events(self, session_id: str, *, meta: Optional[SessionMeta] = None) -> AsyncIterator[ServerSentEvent]:
        """Stream SSE events for a session."""
        if meta is None:
            meta = await self.meta_store.get(session_id)
            if meta is None:
                raise FileNotFoundError(f"session not found: {session_id}")

        initial_status = await self.session_manager.get_status(session_id) or meta.status
        if initial_status != "running":
            for event in await self._emit_completed_snapshot(meta, session_id, initial_status):
                yield event
            return

        queue = await self.session_manager.subscribe(session_id, replay_buffer=True)
        try:
            async for event in self._stream_running_session(
                meta, session_id, initial_status, queue
            ):
                yield event
        finally:
            await self.session_manager.unsubscribe(session_id, queue)

    async def _stream_running_session(
        self,
        meta: SessionMeta,
        session_id: str,
        initial_status: SessionStatus,
        queue: asyncio.Queue,
    ) -> AsyncIterator[ServerSentEvent]:
        """Inner generator for a running session's SSE stream."""
        replayed_messages, replay_overflowed = self._drain_replay(queue)
        if replay_overflowed:
            return

        status = await self.session_manager.get_status(session_id) or initial_status
        projector = await self._build_projector(meta, session_id, replayed_messages)
        snapshot_events = await self._emit_running_snapshot(
            session_id, status, projector
        )
        for event in snapshot_events:
            yield event
        if status != "running":
            return

        while True:
            try:
                message = await asyncio.wait_for(
                    queue.get(), timeout=self.stream_heartbeat_seconds
                )
                events, should_break = await self._dispatch_live_message(
                    message, projector, session_id
                )
                for event in events:
                    yield event
                if should_break:
                    break
            except asyncio.TimeoutError:
                event = await self._handle_heartbeat_timeout(session_id, status, projector)
                if event is not None:
                    yield event
                    break
                continue

    async def _emit_completed_snapshot(
        self, meta: SessionMeta, session_id: str, status: SessionStatus
    ) -> list[ServerSentEvent]:
        """Build snapshot + status events for a non-running session."""
        projector = await self._build_projector(meta, session_id)
        snapshot_payload = await self._with_session_metadata(
            projector.build_snapshot(
                session_id=session_id,
                status=status,
                pending_questions=[],
            ),
            session_id=session_id,
        )
        return [
            self._sse_event("snapshot", snapshot_payload),
            self._sse_event(
                "status",
                self._build_status_event_payload(
                    status=status,
                    session_id=session_id,
                    result_message=projector.last_result,
                ),
            ),
        ]

    async def _emit_running_snapshot(
        self,
        session_id: str,
        status: SessionStatus,
        projector: AssistantStreamProjector,
    ) -> list[ServerSentEvent]:
        """Build snapshot (+ optional terminal status) for a possibly-running session."""
        pending_questions: list[dict[str, Any]] = []
        if status == "running":
            pending_questions = await self.session_manager.get_pending_questions_snapshot(
                session_id
            )
        snapshot_payload = await self._with_session_metadata(
            projector.build_snapshot(
                session_id=session_id,
                status=status,
                pending_questions=pending_questions,
            ),
            session_id=session_id,
        )
        events = [
            self._sse_event("snapshot", snapshot_payload),
        ]
        if status != "running":
            events.append(self._sse_event(
                "status",
                self._build_status_event_payload(
                    status=status,
                    session_id=session_id,
                    result_message=projector.last_result,
                ),
            ))
        return events

    @staticmethod
    def _drain_replay(
        queue: asyncio.Queue,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Drain replayed messages from *queue*, detecting overflow sentinel."""
        replayed: list[dict[str, Any]] = []
        while True:
            try:
                msg = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(msg, dict):
                if msg.get("type") == "_queue_overflow":
                    return replayed, True
                replayed.append(msg)
        return replayed, False

    async def _dispatch_live_message(
        self,
        message: dict[str, Any],
        projector: AssistantStreamProjector,
        session_id: str,
    ) -> tuple[list[ServerSentEvent], bool]:
        """Process one live message. Returns (sse_events, should_break)."""
        events: list[ServerSentEvent] = []

        update = projector.apply_message(message)
        if isinstance(update.get("patch"), dict):
            events.append(
                self._sse_event(
                    "patch",
                    await self._with_session_metadata(
                        update["patch"],
                        session_id=session_id,
                        message=message,
                    ),
                )
            )
        if isinstance(update.get("delta"), dict):
            events.append(
                self._sse_event(
                    "delta",
                    await self._with_session_metadata(
                        update["delta"],
                        session_id=session_id,
                        message=message,
                    ),
                )
            )
        if isinstance(update.get("question"), dict):
            events.append(
                self._sse_event(
                    "question",
                    await self._with_session_metadata(
                        update["question"],
                        session_id=session_id,
                        message=message,
                    ),
                )
            )

        msg_type = message.get("type", "")

        if msg_type == "_queue_overflow":
            return events, True

        if msg_type == "system" and message.get("subtype") == "compact_boundary":
            events.append(self._sse_event("compact", {
                "session_id": session_id,
                "subtype": "compact_boundary",
            }))

        if msg_type == "runtime_status":
            terminal = self._check_runtime_status_terminal(message, session_id)
            if terminal is not None:
                events.append(terminal)
                return events, True

        if msg_type == "result":
            events.append(self._sse_event(
                "status",
                self._build_status_event_payload(
                    status=self._resolve_result_status(message),
                    session_id=session_id,
                    result_message=message,
                ),
            ))
            return events, True

        return events, False

    _TERMINAL_STATUSES = {"idle", "running", "completed", "error", "interrupted"}

    def _check_runtime_status_terminal(
        self, message: dict[str, Any], session_id: str
    ) -> Optional[ServerSentEvent]:
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

    async def _handle_heartbeat_timeout(
        self,
        session_id: str,
        status: SessionStatus,
        projector: AssistantStreamProjector,
    ) -> Optional[ServerSentEvent]:
        """Check session liveness on heartbeat timeout. Returns status event or None."""
        live_status = await self.session_manager.get_status(session_id) or status
        if live_status != "running":
            return self._sse_event(
                "status",
                self._build_status_event_payload(
                    status=live_status,
                    session_id=session_id,
                    result_message=projector.last_result,
                ),
            )
        return None

    @staticmethod
    def _sse_event(event: str, data: dict[str, Any]) -> ServerSentEvent:
        """Build an SSE event for FastAPI's EventSourceResponse."""
        return ServerSentEvent(event=event, data=data)

    async def _build_projector(
        self,
        meta: SessionMeta,
        session_id: str,
        replayed_messages: Optional[list[dict[str, Any]]] = None,
    ) -> AssistantStreamProjector:
        """Build projector state from transcript history + in-memory buffer."""
        history_messages = await asyncio.to_thread(
            self.transcript_adapter.read_raw_messages, meta.sdk_session_id
        )
        projector = AssistantStreamProjector(initial_messages=history_messages)

        # UUID set for primary dedup
        transcript_uuids = {m["uuid"] for m in history_messages if m.get("uuid")}

        # Content fingerprints for tail (current round) - fallback dedup
        tail_fps = self._fingerprint_tail(history_messages)

        buffer = replayed_messages
        if buffer is None:
            buffer = self.session_manager.get_buffered_messages(session_id)

        for msg in buffer or []:
            if not isinstance(msg, dict):
                continue
            msg_type = msg.get("type", "")

            # Non-groupable messages pass through directly
            if msg_type not in {"user", "assistant", "result"}:
                projector.apply_message(msg)
                continue

            # A new real user message in buffer starts a new round;
            # clear tail fingerprints so identical short replies don't collide.
            if self._is_real_user_message(msg):
                tail_fps.clear()

            if not self._is_buffer_duplicate(msg, msg_type, transcript_uuids, tail_fps, history_messages):
                # A local_echo that survived dedup is a genuinely new round;
                # clear tail fingerprints so the upcoming assistant reply
                # isn't falsely matched against a prior round's content.
                if msg_type == "user" and msg.get("local_echo"):
                    tail_fps.clear()
                projector.apply_message(msg)

        return projector

    def _is_buffer_duplicate(
        self,
        msg: dict[str, Any],
        msg_type: str,
        transcript_uuids: set[str],
        tail_fps: set[str],
        history_messages: list[dict[str, Any]],
    ) -> bool:
        """Check if a groupable buffer message duplicates a transcript message."""
        # 1. UUID dedup
        uuid = msg.get("uuid")
        if uuid and uuid in transcript_uuids:
            return True

        # 2. Local echo dedup
        if msg.get("local_echo") and self._echo_in_transcript(msg, history_messages):
            return True

        # 3. Content fingerprint dedup (fallback for UUID-less buffer messages)
        if not uuid and msg_type in {"assistant", "result"}:
            fp = self._fingerprint(msg)
            if fp and fp in tail_fps:
                return True

        return False

    @staticmethod
    def _is_real_user_message(msg: dict[str, Any]) -> bool:
        """Return True if msg is a genuine (non-echo, non-system) user message."""
        if msg.get("type") != "user" or msg.get("local_echo"):
            return False
        content = msg.get("content", "")
        return not (_is_system_injected_user_message(content) or _has_subagent_user_metadata(msg))

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
        result_message: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Build normalized status event payload."""
        message = result_message if isinstance(result_message, dict) else {}
        subtype = message.get("subtype")
        stop_reason = message.get("stop_reason")
        is_error = bool(message.get("is_error"))
        sdk_session_id = None
        explicit_sdk_session_id = message.get("sdk_session_id") or message.get("sdkSessionId")
        if isinstance(explicit_sdk_session_id, str) and explicit_sdk_session_id.strip():
            sdk_session_id = explicit_sdk_session_id.strip()
        else:
            raw_session_id = message.get("session_id") or message.get("sessionId")
            if isinstance(raw_session_id, str):
                normalized_raw_session_id = raw_session_id.strip()
                if normalized_raw_session_id and normalized_raw_session_id != session_id:
                    sdk_session_id = normalized_raw_session_id

        if status == "error" and subtype is None:
            subtype = "error"
        if status == "error":
            is_error = True

        payload = {
            "status": status,
            "subtype": subtype,
            "stop_reason": stop_reason,
            "is_error": is_error,
            "session_id": session_id,
        }
        if sdk_session_id:
            payload["sdk_session_id"] = sdk_session_id
        return payload

    async def _with_session_metadata(
        self,
        payload: dict[str, Any],
        *,
        session_id: str,
        message: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Normalize outward-facing event payloads to ArcReel session ids."""
        normalized = dict(payload)
        normalized["session_id"] = session_id

        sdk_session_id = await self._resolve_sdk_session_id(
            session_id,
            message,
            payload,
        )
        if sdk_session_id:
            normalized["sdk_session_id"] = sdk_session_id
        else:
            normalized.pop("sdk_session_id", None)

        return normalized

    async def _resolve_sdk_session_id(
        self,
        session_id: str,
        *sources: Optional[dict[str, Any]],
    ) -> Optional[str]:
        """Resolve the Claude SDK session id without leaking it into public session_id."""
        for source in sources:
            if not isinstance(source, dict):
                continue

            explicit = source.get("sdk_session_id") or source.get("sdkSessionId")
            if isinstance(explicit, str) and explicit.strip():
                return explicit.strip()

            candidate = source.get("session_id") or source.get("sessionId")
            if isinstance(candidate, str):
                normalized_candidate = candidate.strip()
                if normalized_candidate and normalized_candidate != session_id:
                    return normalized_candidate

        sessions = getattr(self.session_manager, "sessions", None)
        managed = sessions.get(session_id) if isinstance(sessions, dict) else None
        if managed and managed.sdk_session_id:
            return managed.sdk_session_id

        meta = await self.meta_store.get(session_id)
        if meta and meta.sdk_session_id:
            return meta.sdk_session_id

        return None

    @staticmethod
    def _is_groupable_message(message: dict[str, Any]) -> bool:
        """Only user/assistant/result messages are grouped into turns."""
        if not isinstance(message, dict):
            return False
        return message.get("type", "") in {"user", "assistant", "result"}

    @staticmethod
    def _fingerprint_tail(messages: list[dict[str, Any]]) -> set[str]:
        """Build content fingerprints for messages after the last real user message."""
        last_user_idx = AssistantService._find_last_real_user_idx(messages) or 0

        fps: set[str] = set()
        for msg in messages[last_user_idx:]:
            fp = AssistantService._fingerprint(msg)
            if fp:
                fps.add(fp)
        return fps

    @staticmethod
    def _find_last_real_user_idx(messages: list[dict[str, Any]]) -> int | None:
        """Find the latest real user message, skipping system/subagent payloads."""
        for i in range(len(messages) - 1, -1, -1):
            if AssistantService._is_real_user_message(messages[i]):
                return i
        return None

    @staticmethod
    def _fingerprint(message: dict[str, Any]) -> Optional[str]:
        """Build a truncated content fingerprint for dedup."""
        msg_type = message.get("type")
        if msg_type == "assistant":
            content = message.get("content", [])
            parts: list[str] = []
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                tool_id = block.get("id")
                thinking = block.get("thinking")
                if text is not None:
                    parts.append(f"t:{text[:200]}")
                elif tool_id is not None:
                    parts.append(f"u:{tool_id}")
                elif thinking is not None:
                    parts.append(f"th:{thinking[:200]}")
            return f"fp:assistant:{'/'.join(parts)}" if parts else None
        if msg_type == "result":
            return f"fp:result:{message.get('subtype', '')}:{message.get('is_error', False)}"
        return None

    @staticmethod
    def _echo_in_transcript(
        echo_msg: dict[str, Any],
        transcript_msgs: list[dict[str, Any]],
    ) -> bool:
        """Check if a local echo has a matching real message in transcript.

        The comparison must use the last *real* user message, skipping
        system/subagent-injected user payloads. A matching transcript user only
        counts as the current round when it is not older than the local echo;
        otherwise the echo is for a new round that happens to reuse the same
        text. Explicit `result` messages are still treated as round boundaries.
        """
        echo_text = AssistantService._extract_plain_user_content(echo_msg)
        if not echo_text:
            return False

        last_user_idx = AssistantService._find_last_real_user_idx(transcript_msgs)
        if last_user_idx is None:
            return False

        existing_msg = transcript_msgs[last_user_idx]
        # Content must match.
        existing_text = AssistantService._extract_plain_user_content(
            existing_msg
        )
        if existing_text != echo_text:
            return False

        echo_dt = AssistantService._parse_iso_datetime(echo_msg.get("timestamp"))
        existing_dt = AssistantService._parse_iso_datetime(existing_msg.get("timestamp"))
        if echo_dt is not None and existing_dt is not None and existing_dt < echo_dt:
            return False

        # An explicit result marks the prior round complete, so a new echo with
        # the same text must be preserved as a genuinely new round.
        for i in range(last_user_idx + 1, len(transcript_msgs)):
            if transcript_msgs[i].get("type") == "result":
                return False

        # No result after the last real user → round is still in-progress.
        return True

    _extract_plain_user_content = staticmethod(extract_plain_user_content)

    @staticmethod
    def _parse_iso_datetime(value: Any) -> Optional[datetime]:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    # ==================== Lifecycle ====================

    async def shutdown(self) -> None:
        """Shutdown service gracefully."""
        await self.session_manager.shutdown_gracefully()

    # ==================== Skills ====================

    # Display metadata for user-facing skills (label + Lucide icon name)
    _SKILL_DISPLAY_META: dict[str, dict[str, str]] = {
        "manga-workflow":      {"label": "视频工作流",  "icon": "clapperboard"},
        "generate-script":     {"label": "生成剧本",    "icon": "scroll-text"},
        "generate-storyboard": {"label": "生成分镜图",  "icon": "layout-grid"},
        "generate-video":      {"label": "生成视频",    "icon": "film"},
        "generate-characters": {"label": "生成人物图",  "icon": "users"},
        "generate-clues":      {"label": "生成线索图",  "icon": "search"},
        "compose-video":       {"label": "合成视频",    "icon": "scissors"},
    }

    def list_available_skills(self, project_name: Optional[str] = None) -> list[dict[str, str]]:
        """List available skills."""
        if project_name:
            self.pm.get_project_path(project_name)

        source_roots = {
            "agent": self.project_root / "agent_runtime_profile" / ".claude" / "skills",
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
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.exists():
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
                display = self._SKILL_DISPLAY_META.get(metadata["name"])
                if display:
                    skill_entry["label"] = display["label"]
                    skill_entry["icon"] = display["icon"]
                skills.append(skill_entry)

        return skills

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

    @staticmethod
    def _load_project_env(project_root: Path) -> None:
        """Load .env file if exists."""
        env_path = project_root / ".env"
        if not env_path.exists():
            return
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            pass
