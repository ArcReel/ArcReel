"""
Manages ClaudeSDKClient instances with background execution and reconnection support.
"""

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from webui.server.agent_runtime.models import SessionMeta, SessionStatus
from webui.server.agent_runtime.session_store import SessionMetaStore
from webui.server.agent_runtime.transcript_reader import TranscriptReader

try:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    SDK_AVAILABLE = True
except ImportError:
    ClaudeSDKClient = None
    ClaudeAgentOptions = None
    SDK_AVAILABLE = False


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


class SessionManager:
    """Manages all active ClaudeSDKClient instances."""

    DEFAULT_ALLOWED_TOOLS = [
        "Skill", "Read", "Write", "Edit", "MultiEdit",
        "Bash", "Grep", "Glob", "LS",
    ]
    DEFAULT_SETTING_SOURCES = ["user", "project"]

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
        self.max_turns = int(os.environ.get("ASSISTANT_MAX_TURNS", "8"))
        self.cli_path = os.environ.get("ASSISTANT_CLAUDE_CLI_PATH", "").strip() or None

    def _build_options(self, project_name: str, resume_id: Optional[str] = None) -> Any:
        """Build ClaudeAgentOptions for a session."""
        if not SDK_AVAILABLE or ClaudeAgentOptions is None:
            raise RuntimeError("claude_agent_sdk is not installed")

        transcripts_dir = self.data_dir / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)

        return ClaudeAgentOptions(
            cwd=str(self.project_root),
            cli_path=self.cli_path,
            setting_sources=self.DEFAULT_SETTING_SOURCES,
            allowed_tools=self.DEFAULT_ALLOWED_TOOLS,
            max_turns=self.max_turns,
            system_prompt=self.system_prompt,
            include_partial_messages=True,
            resume=resume_id,
        )

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

        options = self._build_options(meta.project_name, meta.sdk_session_id)
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

        # Update status to running
        managed.status = "running"
        self.meta_store.update_status(session_id, "running")

        # Send the query
        await managed.client.query(content)

        # Start consumer task if not running
        if managed.consumer_task is None or managed.consumer_task.done():
            managed.consumer_task = asyncio.create_task(
                self._consume_messages(managed)
            )

    async def _consume_messages(self, managed: ManagedSession) -> None:
        """Consume messages from client and distribute to subscribers."""
        try:
            async for message in managed.client.receive_messages():
                # Serialize message to dict
                msg_dict = self._message_to_dict(message)
                managed.add_message(msg_dict)

                # Check for result message
                if hasattr(message, "subtype") or getattr(message, "type", None) == "result":
                    subtype = getattr(message, "subtype", "")
                    if subtype in ("success", "error"):
                        managed.status = "completed" if subtype == "success" else "error"
                        self.meta_store.update_status(managed.session_id, managed.status)

                        # Update SDK session ID if available
                        sdk_id = getattr(message, "session_id", None)
                        if sdk_id and sdk_id != managed.sdk_session_id:
                            managed.sdk_session_id = sdk_id
                            self.meta_store.update_sdk_session_id(managed.session_id, sdk_id)
                        break

        except asyncio.CancelledError:
            managed.status = "interrupted"
            self.meta_store.update_status(managed.session_id, "interrupted")
            raise
        except Exception:
            managed.status = "error"
            self.meta_store.update_status(managed.session_id, "error")
            raise

    def _message_to_dict(self, message: Any) -> dict[str, Any]:
        """Convert SDK message to dict for JSON serialization."""
        return self._serialize_value(message)

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

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        """Subscribe to session messages. Returns queue for SSE."""
        managed = await self.get_or_connect(session_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

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
