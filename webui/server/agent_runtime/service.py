"""
Assistant service orchestration using ClaudeSDKClient.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from lib.project_manager import ProjectManager
from webui.server.agent_runtime.models import SessionMeta, SessionStatus
from webui.server.agent_runtime.session_manager import SessionManager
from webui.server.agent_runtime.session_store import SessionMetaStore
from webui.server.agent_runtime.stream_projector import AssistantStreamProjector
from webui.server.agent_runtime.transcript_reader import TranscriptReader


class AssistantService:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self._load_project_env(self.project_root)
        self.projects_root = self.project_root / "projects"
        self.data_dir = self.projects_root / ".agent_data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.pm = ProjectManager(self.projects_root)
        self.meta_store = SessionMetaStore(self.data_dir / "sessions.db")
        self.transcript_reader = TranscriptReader(self.data_dir, project_root=self.project_root)
        self.session_manager = SessionManager(
            project_root=self.project_root,
            data_dir=self.data_dir,
            meta_store=self.meta_store,
        )
        self.stream_heartbeat_seconds = int(
            os.environ.get("ASSISTANT_STREAM_HEARTBEAT_SECONDS", "20")
        )

    # ==================== Session CRUD ====================

    async def create_session(self, project_name: str, title: str = "") -> SessionMeta:
        """Create a new session."""
        self.pm.get_project_path(project_name)  # Validate project exists
        normalized_title = title.strip() or f"{project_name} 会话"
        return await self.session_manager.create_session(project_name, normalized_title)

    def list_sessions(
        self,
        project_name: Optional[str] = None,
        status: Optional[SessionStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionMeta]:
        """List sessions."""
        return self.meta_store.list(
            project_name=project_name, status=status, limit=limit, offset=offset
        )

    def get_session(self, session_id: str) -> Optional[SessionMeta]:
        """Get session by ID."""
        meta = self.meta_store.get(session_id)
        if meta and session_id in self.session_manager.sessions:
            # Update status from live session
            managed = self.session_manager.sessions[session_id]
            meta = SessionMeta(
                **{**meta.model_dump(), "status": managed.status}
            )
        return meta

    def update_session_title(self, session_id: str, title: str) -> Optional[SessionMeta]:
        """Update session title."""
        if self.meta_store.get(session_id) is None:
            return None
        normalized = title.strip() or "未命名会话"
        if not self.meta_store.update_title(session_id, normalized):
            return None
        return self.meta_store.get(session_id)

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
            except Exception:
                pass
            del self.session_manager.sessions[session_id]

        return self.meta_store.delete(session_id)

    # ==================== Messages ====================

    async def get_snapshot(self, session_id: str) -> dict[str, Any]:
        """Build a normalized v2 snapshot for history and reconnect."""
        meta = self.meta_store.get(session_id)
        if meta is None:
            raise FileNotFoundError(f"session not found: {session_id}")

        status = self.session_manager.get_status(session_id) or meta.status
        # Read buffer once to avoid inconsistency from concurrent mutations.
        buffered_messages = self.session_manager.get_buffered_messages(session_id)
        raw_messages = self._build_initial_raw_messages(
            meta, session_id, buffered_messages=buffered_messages
        )
        projector = AssistantStreamProjector(initial_messages=raw_messages)

        for message in buffered_messages:
            if self._is_groupable_message(message):
                continue
            projector.apply_message(message)

        pending_questions = []
        if status == "running":
            pending_questions = await self.session_manager.get_pending_questions_snapshot(
                session_id
            )
        return projector.build_snapshot(
            session_id=session_id,
            status=status,
            pending_questions=pending_questions,
        )

    async def send_message(self, session_id: str, content: str) -> dict[str, Any]:
        """Send a message to the session."""
        text = content.strip()
        if not text:
            raise ValueError("消息内容不能为空")

        meta = self.meta_store.get(session_id)
        if meta is None:
            raise FileNotFoundError(f"session not found: {session_id}")

        await self.session_manager.send_message(session_id, text)
        return {"status": "accepted", "session_id": session_id}

    async def answer_user_question(
        self,
        session_id: str,
        question_id: str,
        answers: dict[str, str],
    ) -> dict[str, Any]:
        """Submit answers for a pending AskUserQuestion."""
        meta = self.meta_store.get(session_id)
        if meta is None:
            raise FileNotFoundError(f"session not found: {session_id}")
        await self.session_manager.answer_user_question(session_id, question_id, answers)
        return {"status": "accepted", "session_id": session_id, "question_id": question_id}

    async def interrupt_session(self, session_id: str) -> dict[str, Any]:
        """Interrupt a running session."""
        meta = self.meta_store.get(session_id)
        if meta is None:
            raise FileNotFoundError(f"session not found: {session_id}")
        session_status = await self.session_manager.interrupt_session(session_id)
        return {
            "status": "accepted",
            "session_id": session_id,
            "session_status": session_status,
        }

    # ==================== Streaming ====================

    async def stream_events(self, session_id: str) -> AsyncIterator[str]:
        """Stream SSE events for a session."""
        meta = self.meta_store.get(session_id)
        if meta is None:
            raise FileNotFoundError(f"session not found: {session_id}")

        initial_status = self.session_manager.get_status(session_id) or meta.status
        if initial_status != "running":
            for event in self._emit_completed_snapshot(meta, session_id, initial_status):
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
    ) -> AsyncIterator[str]:
        """Inner generator for a running session's SSE stream."""
        replayed_messages, replay_overflowed = self._drain_replay(queue)
        if replay_overflowed:
            return

        status = self.session_manager.get_status(session_id) or initial_status
        projector = self._build_projector(meta, session_id, replayed_messages)
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
                events, should_break = self._dispatch_live_message(
                    message, projector, session_id
                )
                for event in events:
                    yield event
                if should_break:
                    break
            except asyncio.TimeoutError:
                event = self._handle_heartbeat_timeout(session_id, status, projector)
                if event is not None:
                    yield event
                    break
                yield self._sse_keepalive_comment()

    def _emit_completed_snapshot(
        self, meta: SessionMeta, session_id: str, status: SessionStatus
    ) -> list[str]:
        """Build snapshot + status events for a non-running session."""
        projector = self._build_projector(meta, session_id)
        return [
            self._sse_event(
                "snapshot",
                projector.build_snapshot(
                    session_id=session_id,
                    status=status,
                    pending_questions=[],
                ),
            ),
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
    ) -> list[str]:
        """Build snapshot (+ optional terminal status) for a possibly-running session."""
        pending_questions: list[dict[str, Any]] = []
        if status == "running":
            pending_questions = await self.session_manager.get_pending_questions_snapshot(
                session_id
            )
        events = [
            self._sse_event(
                "snapshot",
                projector.build_snapshot(
                    session_id=session_id,
                    status=status,
                    pending_questions=pending_questions,
                ),
            ),
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

    def _dispatch_live_message(
        self,
        message: dict[str, Any],
        projector: AssistantStreamProjector,
        session_id: str,
    ) -> tuple[list[str], bool]:
        """Process one live message. Returns (sse_events, should_break)."""
        events: list[str] = []

        update = projector.apply_message(message)
        if isinstance(update.get("patch"), dict):
            events.append(self._sse_event("patch", update["patch"]))
        if isinstance(update.get("delta"), dict):
            events.append(self._sse_event("delta", update["delta"]))
        if isinstance(update.get("question"), dict):
            events.append(self._sse_event("question", update["question"]))

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
    ) -> Optional[str]:
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

    def _handle_heartbeat_timeout(
        self,
        session_id: str,
        status: SessionStatus,
        projector: AssistantStreamProjector,
    ) -> Optional[str]:
        """Check session liveness on heartbeat timeout. Returns status event or None."""
        live_status = self.session_manager.get_status(session_id) or status
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
    def _sse_event(event: str, data: dict[str, Any]) -> str:
        """Format SSE event."""
        json_data = json.dumps(data, ensure_ascii=False)
        return f"event: {event}\ndata: {json_data}\n\n"

    @staticmethod
    def _sse_keepalive_comment() -> str:
        """Format SSE comment heartbeat without introducing extra event types."""
        return ": keepalive\n\n"

    def _build_projector(
        self,
        meta: SessionMeta,
        session_id: str,
        replayed_messages: Optional[list[dict[str, Any]]] = None,
    ) -> AssistantStreamProjector:
        """Build projector state from transcript history + in-memory buffer."""
        raw_messages = self._build_initial_raw_messages(
            meta=meta,
            session_id=session_id,
            buffered_messages=replayed_messages,
        )
        projector = AssistantStreamProjector(initial_messages=raw_messages)
        for message in replayed_messages or []:
            if self._is_groupable_message(message):
                continue
            projector.apply_message(message)
        return projector

    def _build_initial_raw_messages(
        self,
        meta: SessionMeta,
        session_id: str,
        buffered_messages: Optional[list[dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        """Build deduped raw conversation history used by turn grouping."""
        history_messages = self.transcript_reader.read_raw_messages(
            session_id,
            meta.sdk_session_id,
            project_name=meta.project_name,
        )
        runtime_buffer = buffered_messages
        if runtime_buffer is None:
            runtime_buffer = self.session_manager.get_buffered_messages(session_id)

        groupable_runtime = [
            message
            for message in (runtime_buffer or [])
            if self._is_groupable_message(message)
            # Skip buffer assistant/result messages that lack uuid — these are
            # SDK-serialized duplicates of transcript entries.  The CLI writes
            # messages to the JSONL transcript with a uuid wrapper; SDK objects
            # (AssistantMessage, ResultMessage) don't carry uuid, so they can
            # never be reliably deduplicated against transcript entries.
            # Only user messages (local_echo with uuid) may be the sole source
            # for a round that the CLI hasn't persisted yet.
            and (message.get("type") == "user" or message.get("uuid"))
        ]
        return self._merge_raw_messages(history_messages, groupable_runtime)

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
        normalized_session_id = message.get("session_id") or session_id

        if status == "error" and subtype is None:
            subtype = "error"
        if status == "error":
            is_error = True

        return {
            "status": status,
            "subtype": subtype,
            "stop_reason": stop_reason,
            "is_error": is_error,
            "session_id": normalized_session_id,
        }

    @staticmethod
    def _is_groupable_message(message: dict[str, Any]) -> bool:
        """Only user/assistant/result messages are grouped into turns."""
        if not isinstance(message, dict):
            return False
        return message.get("type", "") in {"user", "assistant", "result"}

    @staticmethod
    def _message_key(message: dict[str, Any]) -> str:
        """Build dedupe key for raw messages merged from transcript and memory buffer."""
        uuid = message.get("uuid")
        if uuid:
            return f"uuid:{uuid}"
        return json.dumps(message, sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _content_key(message: dict[str, Any]) -> Optional[str]:
        """Build a content-based key for cross-source dedup.

        Transcript messages carry a uuid assigned by the CLI wrapper while
        buffer messages converted from SDK objects often lack one.  When the
        same logical message appears in both sources, _message_key produces
        different keys (uuid vs json.dumps) and dedup fails.

        This helper normalises on (type, content) so that a buffer message
        without uuid can still be recognised as a duplicate of a transcript
        entry that has one.

        Returns None for message types where content-based matching is unsafe
        (e.g. user messages – the user may legitimately send the same text
        twice).
        """
        msg_type = message.get("type")
        if msg_type == "assistant":
            content = message.get("content", [])
            # Normalise content blocks: SDK dataclass serialization omits
            # the ``type`` field that the CLI transcript includes.  Extract
            # only the fields both sources share so the key matches.
            parts: list[str] = []
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                tool_id = block.get("id")
                if text is not None:
                    parts.append(f"t:{text}")
                elif tool_id is not None:
                    parts.append(f"u:{tool_id}")
            return f"content:assistant:{'/'.join(parts)}" if parts else None
        if msg_type == "result":
            # Include session_id and timestamp to avoid cross-round collisions
            # when multiple results share the same subtype/is_error.
            sid = message.get("session_id", "")
            ts = message.get("timestamp", "")
            return f"content:result:{message.get('subtype', '')}:{message.get('is_error', False)}:{sid}:{ts}"
        return None

    def _merge_raw_messages(
        self,
        history_raw: list[dict[str, Any]],
        buffered_raw: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge transcript raw messages with in-memory buffer, preserving order."""
        merged = list(history_raw or [])
        seen_keys, seen_content_keys = self._build_seen_sets(merged)

        for msg in buffered_raw or []:
            if not isinstance(msg, dict):
                continue
            if self._should_skip_local_echo(msg, merged):
                continue
            if self._is_duplicate(msg, seen_keys, seen_content_keys):
                continue
            seen_keys.add(self._message_key(msg))
            merged.append(msg)
        return merged

    def _build_seen_sets(
        self, messages: list[dict[str, Any]]
    ) -> tuple[set[str], set[str]]:
        """Build uuid-based and content-based seen sets from existing messages."""
        seen_keys: set[str] = set()
        seen_content_keys: set[str] = set()
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            seen_keys.add(self._message_key(msg))
            ck = self._content_key(msg)
            if ck:
                seen_content_keys.add(ck)
        return seen_keys, seen_content_keys

    def _is_duplicate(
        self,
        msg: dict[str, Any],
        seen_keys: set[str],
        seen_content_keys: set[str],
    ) -> bool:
        """Check whether *msg* duplicates an already-seen message."""
        key = self._message_key(msg)
        if key in seen_keys:
            return True
        # For messages without uuid, fall back to content-based dedup
        if not msg.get("uuid"):
            ck = self._content_key(msg)
            if ck and ck in seen_content_keys:
                return True
        return False

    @staticmethod
    def _extract_plain_user_content(message: dict[str, Any]) -> Optional[str]:
        """Extract plain text from a user message payload."""
        if message.get("type") != "user":
            return None
        content = message.get("content")
        if isinstance(content, str):
            text = content.strip()
            return text or None
        if (
            isinstance(content, list)
            and len(content) == 1
            and isinstance(content[0], dict)
        ):
            block = content[0]
            block_type = block.get("type")
            if block_type in {"text", None}:
                text = block.get("text")
                if isinstance(text, str):
                    text = text.strip()
                    return text or None
        return None

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

    def _should_skip_local_echo(
        self,
        message: dict[str, Any],
        merged_messages: list[dict[str, Any]],
    ) -> bool:
        """Drop local echo once a matching real transcript user message is present."""
        if not message.get("local_echo"):
            return False

        echo_text = self._extract_plain_user_content(message)
        if not echo_text:
            return False

        echo_ts = self._parse_iso_datetime(message.get("timestamp"))
        for existing in reversed(merged_messages):
            if not isinstance(existing, dict):
                continue
            if existing.get("type") != "user" or existing.get("local_echo"):
                continue
            if self._extract_plain_user_content(existing) != echo_text:
                continue
            if echo_ts is None:
                return True
            existing_ts = self._parse_iso_datetime(existing.get("timestamp"))
            if existing_ts is None:
                return True
            if existing_ts >= (echo_ts - timedelta(seconds=5)):
                return True

        return False

    # ==================== Lifecycle ====================

    async def shutdown(self) -> None:
        """Shutdown service gracefully."""
        await self.session_manager.shutdown_gracefully()

    # ==================== Skills ====================

    def list_available_skills(self, project_name: Optional[str] = None) -> list[dict[str, str]]:
        """List available skills."""
        if project_name:
            self.pm.get_project_path(project_name)

        source_roots = {
            "project": self.project_root / ".claude" / "skills",
            "user": Path.home() / ".claude" / "skills",
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

                key = f"{scope}:{metadata['name']}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                skills.append({
                    "name": metadata["name"],
                    "description": metadata["description"],
                    "scope": scope,
                    "path": str(skill_file),
                })

        return skills

    @staticmethod
    def _load_skill_metadata(skill_file: Path, fallback_name: str) -> dict[str, str]:
        """Load skill metadata from SKILL.md."""
        content = skill_file.read_text(encoding="utf-8", errors="ignore")
        name = fallback_name
        description = ""

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

        return {"name": name, "description": description}

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
