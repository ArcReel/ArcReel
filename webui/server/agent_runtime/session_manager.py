"""
Manages ClaudeSDKClient instances with background execution and reconnection support.
"""

import asyncio
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from webui.server.agent_runtime.models import SessionMeta, SessionStatus
from webui.server.agent_runtime.session_store import SessionMetaStore
from webui.server.agent_runtime.transcript_reader import TranscriptReader

try:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    from claude_agent_sdk.types import HookMatcher, PermissionResultAllow
    try:
        from claude_agent_sdk.types import PermissionResultDeny
    except ImportError:
        PermissionResultDeny = None

    SDK_AVAILABLE = True
except ImportError:
    ClaudeSDKClient = None
    ClaudeAgentOptions = None
    HookMatcher = None
    PermissionResultAllow = None
    PermissionResultDeny = None
    SDK_AVAILABLE = False


def _utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class PendingQuestion:
    """Tracks a pending AskUserQuestion request."""
    question_id: str
    payload: dict[str, Any]
    answer_future: asyncio.Future[dict[str, str]]


@dataclass
class ManagedSession:
    """A managed ClaudeSDKClient session."""
    session_id: str
    client: Any  # ClaudeSDKClient
    sdk_session_id: Optional[str] = None
    status: SessionStatus = "idle"
    message_buffer: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    consumer_task: Optional[asyncio.Task] = None
    buffer_max_size: int = 100
    pending_questions: dict[str, PendingQuestion] = field(default_factory=dict)
    pending_user_echoes: list[str] = field(default_factory=list)
    interrupt_requested: bool = False

    def add_message(self, message: dict[str, Any]) -> None:
        """Add message to buffer and notify subscribers."""
        self.message_buffer.append(message)
        if len(self.message_buffer) > self.buffer_max_size:
            self.message_buffer.pop(0)
        for queue in self.subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    def clear_buffer(self) -> None:
        """Clear message buffer after session completes."""
        self.message_buffer.clear()

    def add_pending_question(self, payload: dict[str, Any]) -> PendingQuestion:
        """Register a pending AskUserQuestion payload."""
        question_id = str(payload.get("question_id") or f"aq_{uuid4().hex}")
        payload["question_id"] = question_id
        future: asyncio.Future[dict[str, str]] = asyncio.get_running_loop().create_future()
        pending = PendingQuestion(
            question_id=question_id,
            payload=payload,
            answer_future=future,
        )
        self.pending_questions[question_id] = pending
        return pending

    def resolve_pending_question(self, question_id: str, answers: dict[str, str]) -> bool:
        """Resolve a pending AskUserQuestion with user answers."""
        pending = self.pending_questions.pop(question_id, None)
        if not pending:
            return False
        if not pending.answer_future.done():
            pending.answer_future.set_result(answers)
        return True

    def cancel_pending_questions(self, reason: str = "session closed") -> None:
        """Cancel all pending AskUserQuestion waiters."""
        for pending in list(self.pending_questions.values()):
            if not pending.answer_future.done():
                pending.answer_future.set_exception(
                    RuntimeError(reason)
                )
        self.pending_questions.clear()

    def get_pending_question_payloads(self) -> list[dict[str, Any]]:
        """Return unresolved AskUserQuestion payloads for reconnect snapshot."""
        return [pending.payload for pending in self.pending_questions.values()]


class SessionManager:
    """Manages all active ClaudeSDKClient instances."""

    DEFAULT_ALLOWED_TOOLS = [
        "Skill", "Read", "Write", "Edit", "MultiEdit",
        "Bash", "Grep", "Glob", "LS", "AskUserQuestion",
    ]
    DEFAULT_SETTING_SOURCES = ["user", "project"]

    # SDK message class name to type mapping
    _MESSAGE_TYPE_MAP = {
        "UserMessage": "user",
        "AssistantMessage": "assistant",
        "ResultMessage": "result",
        "SystemMessage": "system",
        "StreamEvent": "stream_event",
    }

    def __init__(
        self,
        project_root: Path,
        data_dir: Path,
        meta_store: SessionMetaStore,
    ):
        self.project_root = Path(project_root)
        self.data_dir = Path(data_dir)
        self.meta_store = meta_store
        self.transcript_reader = TranscriptReader(data_dir, project_root=project_root)
        self.sessions: dict[str, ManagedSession] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from environment."""
        self.system_prompt = os.environ.get(
            "ASSISTANT_SYSTEM_PROMPT",
            "你是视频项目协作助手。优先复用项目中的 Skills 与现有文件结构，避免擅自改写数据格式。"
        ).strip()
        max_turns_env = os.environ.get("ASSISTANT_MAX_TURNS", "").strip()
        self.max_turns = int(max_turns_env) if max_turns_env else None
        self.cli_path = os.environ.get("ASSISTANT_CLAUDE_CLI_PATH", "").strip() or None

    def _build_options(
        self,
        project_name: str,
        resume_id: Optional[str] = None,
        can_use_tool: Optional[Callable[[str, dict[str, Any], Any], Any]] = None,
    ) -> Any:
        """Build ClaudeAgentOptions for a session."""
        if not SDK_AVAILABLE or ClaudeAgentOptions is None:
            raise RuntimeError("claude_agent_sdk is not installed")

        transcripts_dir = self.data_dir / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        project_cwd = self._resolve_project_cwd(project_name)

        hooks = None
        if can_use_tool is not None and HookMatcher is not None:
            # Official Python SDK guidance: keep stream open when using can_use_tool.
            hooks = {
                "PreToolUse": [
                    HookMatcher(matcher=None, hooks=[self._keep_stream_open_hook]),
                ]
            }

        return ClaudeAgentOptions(
            cwd=str(project_cwd),
            cli_path=self.cli_path,
            setting_sources=self.DEFAULT_SETTING_SOURCES,
            allowed_tools=self.DEFAULT_ALLOWED_TOOLS,
            max_turns=self.max_turns,
            system_prompt=self.system_prompt,
            include_partial_messages=True,
            resume=resume_id,
            can_use_tool=can_use_tool,
            hooks=hooks,
        )

    @staticmethod
    async def _keep_stream_open_hook(_input_data: dict[str, Any], _tool_use_id: str | None, _context: Any) -> dict[str, bool]:
        """Required keep-alive hook for Python can_use_tool callback."""
        return {"continue_": True}

    def _resolve_project_cwd(self, project_name: str) -> Path:
        """Resolve and validate per-session project working directory."""
        projects_root = (self.project_root / "projects").resolve()
        project_cwd = (projects_root / project_name).resolve()
        try:
            project_cwd.relative_to(projects_root)
        except ValueError as exc:
            raise ValueError("invalid project name") from exc
        if not project_cwd.exists() or not project_cwd.is_dir():
            raise FileNotFoundError(f"project not found: {project_name}")
        return project_cwd

    async def create_session(self, project_name: str, title: str = "") -> SessionMeta:
        """Create a new session."""
        meta = self.meta_store.create(project_name, title)
        return meta

    async def get_or_connect(self, session_id: str) -> ManagedSession:
        """Get existing managed session or create new connection."""
        if session_id in self.sessions:
            return self.sessions[session_id]

        meta = self.meta_store.get(session_id)
        if meta is None:
            raise FileNotFoundError(f"session not found: {session_id}")

        if not SDK_AVAILABLE or ClaudeSDKClient is None:
            raise RuntimeError("claude_agent_sdk is not installed")

        options = self._build_options(
            meta.project_name,
            meta.sdk_session_id,
            can_use_tool=self._build_can_use_tool_callback(session_id),
        )
        client = ClaudeSDKClient(options=options)
        await client.connect()

        managed = ManagedSession(
            session_id=session_id,
            client=client,
            sdk_session_id=meta.sdk_session_id,
            status=meta.status if meta.status != "idle" else "idle",
        )
        self.sessions[session_id] = managed
        return managed

    async def send_message(self, session_id: str, content: str) -> None:
        """Send a message and start background consumer."""
        managed = await self.get_or_connect(session_id)
        self._prune_transient_buffer(managed)

        # Update status to running
        managed.status = "running"
        self.meta_store.update_status(session_id, "running")

        # Echo user input immediately so live SSE shows it even when SDK stream
        # doesn't replay user messages in real time.
        managed.pending_user_echoes.append(content)
        if len(managed.pending_user_echoes) > 20:
            managed.pending_user_echoes.pop(0)
        managed.add_message(self._build_user_echo_message(content))

        # Send the query
        await managed.client.query(content)

        # Start consumer task if not running
        if managed.consumer_task is None or managed.consumer_task.done():
            managed.consumer_task = asyncio.create_task(
                self._consume_messages(managed)
            )

    async def interrupt_session(self, session_id: str) -> SessionStatus:
        """Interrupt a running session."""
        meta = self.meta_store.get(session_id)
        if meta is None:
            raise FileNotFoundError(f"session not found: {session_id}")

        managed = self.sessions.get(session_id)
        if managed is None:
            if meta.status == "running":
                self.meta_store.update_status(session_id, "interrupted")
                return "interrupted"
            return meta.status

        if managed.status != "running":
            return managed.status

        managed.pending_user_echoes.clear()
        managed.interrupt_requested = True
        managed.cancel_pending_questions("session interrupted by user")

        await managed.client.interrupt()
        return managed.status

    async def _consume_messages(self, managed: ManagedSession) -> None:
        """Consume messages from client and distribute to subscribers."""
        try:
            async for message in managed.client.receive_response():
                # Serialize message to dict
                msg_dict = self._message_to_dict(message)
                if not isinstance(msg_dict, dict):
                    continue

                if self._is_duplicate_user_echo(managed, msg_dict):
                    self._maybe_update_sdk_session_id(managed, message, msg_dict)
                    continue

                if msg_dict.get("type") == "result":
                    msg_dict["session_status"] = self._resolve_result_status(
                        msg_dict,
                        interrupt_requested=managed.interrupt_requested,
                    )
                managed.add_message(msg_dict)
                self._maybe_update_sdk_session_id(managed, message, msg_dict)

                if msg_dict.get("type") != "result":
                    continue

                managed.pending_user_echoes.clear()
                managed.cancel_pending_questions("session completed")
                final_status = str(msg_dict.get("session_status") or "").strip() or self._resolve_result_status(
                    msg_dict,
                    interrupt_requested=managed.interrupt_requested,
                )
                managed.status = final_status
                self.meta_store.update_status(managed.session_id, final_status)
                managed.interrupt_requested = False
                self._prune_transient_buffer(managed)

        except asyncio.CancelledError:
            managed.pending_user_echoes.clear()
            managed.cancel_pending_questions("session interrupted")
            managed.status = "interrupted"
            self.meta_store.update_status(managed.session_id, "interrupted")
            managed.interrupt_requested = False
            self._prune_transient_buffer(managed)
            raise
        except Exception:
            managed.pending_user_echoes.clear()
            managed.cancel_pending_questions("session error")
            managed.status = "error"
            self.meta_store.update_status(managed.session_id, "error")
            managed.interrupt_requested = False
            self._prune_transient_buffer(managed)
            raise

    @staticmethod
    def _resolve_result_status(
        result_message: dict[str, Any],
        interrupt_requested: bool = False,
    ) -> SessionStatus:
        """Map SDK result subtype/is_error to runtime session status."""
        subtype = str(result_message.get("subtype") or "").strip().lower()
        is_error = bool(result_message.get("is_error"))
        if interrupt_requested:
            if subtype in {"interrupted", "interrupt"}:
                return "interrupted"
            if is_error or subtype.startswith("error"):
                return "interrupted"
        if is_error or subtype.startswith("error"):
            return "error"
        return "completed"

    def _build_can_use_tool_callback(self, session_id: str):
        """Create per-session can_use_tool callback for AskUserQuestion."""

        async def _can_use_tool(
            tool_name: str,
            input_data: dict[str, Any],
            _context: Any,
        ) -> Any:
            if PermissionResultAllow is None:
                raise RuntimeError("claude_agent_sdk is not installed")

            normalized_tool = str(tool_name or "").strip().lower()
            if normalized_tool != "askuserquestion":
                return PermissionResultAllow(updated_input=input_data)

            managed = self.sessions.get(session_id)
            if managed is None:
                return PermissionResultAllow(updated_input=input_data)

            raw_questions = input_data.get("questions")
            questions = raw_questions if isinstance(raw_questions, list) else []
            payload = {
                "type": "ask_user_question",
                "question_id": f"aq_{uuid4().hex}",
                "tool_name": tool_name,
                "questions": questions,
                "timestamp": _utc_now_iso(),
            }
            pending = managed.add_pending_question(payload)
            managed.add_message(payload)

            try:
                answers = await pending.answer_future
            except Exception as exc:
                if PermissionResultDeny is not None:
                    return PermissionResultDeny(
                        message=str(exc) or "session interrupted by user",
                        interrupt=True,
                    )
                raise
            merged_input = dict(input_data or {})
            merged_input["answers"] = answers
            return PermissionResultAllow(updated_input=merged_input)

        return _can_use_tool

    def _message_to_dict(self, message: Any) -> dict[str, Any]:
        """Convert SDK message to dict for JSON serialization."""
        msg_dict = self._serialize_value(message)

        # Infer and add message type if not present
        if isinstance(msg_dict, dict) and "type" not in msg_dict:
            msg_type = self._infer_message_type(message)
            if msg_type:
                msg_dict["type"] = msg_type

        return msg_dict

    @staticmethod
    def _build_user_echo_message(content: str) -> dict[str, Any]:
        """Build a synthetic user message for real-time UI echo."""
        return {
            "type": "user",
            "content": content,
            "uuid": f"local-user-{uuid4().hex}",
            "timestamp": _utc_now_iso(),
            "local_echo": True,
        }

    @staticmethod
    def _prune_transient_buffer(managed: ManagedSession) -> None:
        """Drop transient runtime events that should not leak into next round snapshots."""
        if not managed.message_buffer:
            return
        managed.message_buffer = [
            message
            for message in managed.message_buffer
            if message.get("type") not in {"stream_event", "runtime_status"}
        ]

    @staticmethod
    def _build_runtime_status_message(
        status: SessionStatus,
        session_id: str,
    ) -> dict[str, Any]:
        """Build runtime-only status message for SSE wake-up."""
        return {
            "type": "runtime_status",
            "status": status,
            "subtype": status,
            "stop_reason": None,
            "is_error": status == "error",
            "session_id": session_id,
            "uuid": f"runtime-status-{uuid4().hex}",
            "timestamp": _utc_now_iso(),
        }

    @staticmethod
    def _extract_plain_user_content(message: dict[str, Any]) -> Optional[str]:
        """Extract plain text from a real user message for echo dedupe."""
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

    def _is_duplicate_user_echo(
        self,
        managed: ManagedSession,
        message: dict[str, Any],
    ) -> bool:
        """Skip SDK-replayed user message if it matches local echo queue."""
        if not managed.pending_user_echoes:
            return False
        incoming = self._extract_plain_user_content(message)
        if not incoming:
            return False
        expected = managed.pending_user_echoes[0].strip()
        if incoming != expected:
            return False
        managed.pending_user_echoes.pop(0)
        return True

    def _maybe_update_sdk_session_id(
        self,
        managed: ManagedSession,
        message: Any,
        msg_dict: dict[str, Any],
    ) -> None:
        """Persist SDK session id as soon as it appears in stream messages."""
        sdk_id = self._extract_sdk_session_id(message, msg_dict)
        if not sdk_id or sdk_id == managed.sdk_session_id:
            return
        managed.sdk_session_id = sdk_id
        self.meta_store.update_sdk_session_id(managed.session_id, sdk_id)

    @staticmethod
    def _extract_sdk_session_id(
        message: Any, msg_dict: dict[str, Any]
    ) -> Optional[str]:
        """Extract SDK session id from either serialized payload or raw object."""
        sdk_id = None
        if isinstance(msg_dict, dict):
            sdk_id = msg_dict.get("session_id") or msg_dict.get("sessionId")
        if sdk_id:
            return str(sdk_id)
        raw_sdk_id = getattr(message, "session_id", None) or getattr(
            message, "sessionId", None
        )
        if raw_sdk_id:
            return str(raw_sdk_id)
        return None

    def _infer_message_type(self, message: Any) -> Optional[str]:
        """Infer message type from SDK message class name."""
        class_name = type(message).__name__
        return self._MESSAGE_TYPE_MAP.get(class_name)

    def _serialize_value(self, value: Any) -> Any:
        """Recursively serialize a value to JSON-safe types."""
        if value is None or isinstance(value, (bool, int, float, str)):
            return value

        if isinstance(value, dict):
            return {k: self._serialize_value(v) for k, v in value.items()}

        if isinstance(value, (list, tuple)):
            return [self._serialize_value(item) for item in value]

        # Pydantic models
        if hasattr(value, "model_dump"):
            dumped = value.model_dump()
            return self._serialize_value(dumped)

        # Dataclasses or objects with __dict__
        if hasattr(value, "__dict__"):
            return {
                k: self._serialize_value(v)
                for k, v in value.__dict__.items()
                if not k.startswith("_")
            }

        # Fallback: convert to string
        return str(value)

    async def get_message_buffer_snapshot(self, session_id: str) -> list[dict[str, Any]]:
        """Get current message buffer without creating a new SDK connection."""
        managed = self.sessions.get(session_id)
        if not managed:
            return []
        return list(managed.message_buffer)

    def get_buffered_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Sync helper for consumers that only need in-memory buffer state."""
        managed = self.sessions.get(session_id)
        if not managed:
            return []
        return list(managed.message_buffer)

    async def get_pending_questions_snapshot(self, session_id: str) -> list[dict[str, Any]]:
        """Get unresolved AskUserQuestion payloads for reconnect."""
        managed = self.sessions.get(session_id)
        if not managed:
            return []
        return managed.get_pending_question_payloads()

    async def answer_user_question(
        self,
        session_id: str,
        question_id: str,
        answers: dict[str, str],
    ) -> None:
        """Resolve AskUserQuestion answers for a running session."""
        managed = self.sessions.get(session_id)
        if managed is None:
            raise ValueError("会话未运行或无待回答问题")
        if not managed.resolve_pending_question(question_id, answers):
            raise ValueError("未找到待回答的问题")

    async def subscribe(self, session_id: str, replay_buffer: bool = True) -> asyncio.Queue:
        """Subscribe to session messages. Returns queue for SSE."""
        managed = await self.get_or_connect(session_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        if replay_buffer:
            # Replay buffered messages
            for msg in managed.message_buffer:
                try:
                    queue.put_nowait(msg)
                except asyncio.QueueFull:
                    break

        managed.subscribers.add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        """Unsubscribe from session messages."""
        if session_id in self.sessions:
            self.sessions[session_id].subscribers.discard(queue)

    def get_status(self, session_id: str) -> Optional[SessionStatus]:
        """Get session status."""
        if session_id in self.sessions:
            return self.sessions[session_id].status
        meta = self.meta_store.get(session_id)
        return meta.status if meta else None

    async def shutdown_gracefully(self, timeout: float = 30.0) -> None:
        """Gracefully shutdown all sessions."""
        for session_id, managed in list(self.sessions.items()):
            managed.cancel_pending_questions("session shutdown")
            if managed.status == "running":
                # Wait for current turn
                if managed.consumer_task and not managed.consumer_task.done():
                    try:
                        await asyncio.wait_for(managed.consumer_task, timeout=timeout)
                    except asyncio.TimeoutError:
                        await managed.client.interrupt()
                        managed.consumer_task.cancel()

                managed.status = "interrupted"
                self.meta_store.update_status(session_id, "interrupted")

            # Disconnect client
            try:
                await managed.client.disconnect()
            except Exception:
                pass

        self.sessions.clear()
