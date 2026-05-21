# ClaudeSDKClient 迁移实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将项目从 `query()` 迁移到 `ClaudeSDKClient`，实现原生多轮对话、后台持续执行、断线重连功能。

**Architecture:** 使用 `SessionManager` 管理所有活跃的 `ClaudeSDKClient` 实例，SQLite 只存储会话元数据，消息历史从 SDK 的 transcript 文件按需读取。前端直接适配 SDK 消息格式，无转换层。

**Tech Stack:** Python 3.10+, FastAPI, claude_agent_sdk, SQLite, React, EventSource (SSE)

---

## Task 1: 创建数据目录结构

**Files:**
- Create: `projects/.agent_data/.gitkeep`
- Create: `projects/.agent_data/transcripts/.gitkeep`

**Step 1: 创建目录结构**

```bash
mkdir -p projects/.agent_data/transcripts
touch projects/.agent_data/.gitkeep
touch projects/.agent_data/transcripts/.gitkeep
```

**Step 2: 更新 .gitignore**

添加以下行到 `.gitignore`（如果不存在）：

```
# Agent data (transcripts are large, only keep structure)
projects/.agent_data/transcripts/*.json
projects/.agent_data/sessions.db
```

**Step 3: Commit**

```bash
git add projects/.agent_data/.gitkeep projects/.agent_data/transcripts/.gitkeep .gitignore
git commit -m "chore: add agent data directory structure"
```

---

## Task 2: 重写 models.py - 简化数据模型

**Files:**
- Modify: `webui/server/agent_runtime/models.py`

**Step 1: 重写 models.py**

```python
"""
Agent runtime data models.
"""

from typing import Literal, Optional

from pydantic import BaseModel

SessionStatus = Literal["idle", "running", "completed", "error", "interrupted"]


class SessionMeta(BaseModel):
    """Session metadata stored in SQLite."""
    id: str
    sdk_session_id: Optional[str] = None
    project_name: str
    title: str = ""
    status: SessionStatus = "idle"
    transcript_path: Optional[str] = None
    created_at: str
    updated_at: str
```

**Step 2: Commit**

```bash
git add webui/server/agent_runtime/models.py
git commit -m "refactor(models): simplify to SessionMeta only, remove AgentMessage"
```

---

## Task 3: 重写 session_store.py - SessionMetaStore

**Files:**
- Modify: `webui/server/agent_runtime/session_store.py`

**Step 1: 重写为 SessionMetaStore**

```python
"""
SQLite-based session metadata storage.
"""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from webui.server.agent_runtime.models import SessionMeta, SessionStatus


class SessionMetaStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_session(row: sqlite3.Row) -> SessionMeta:
        return SessionMeta(
            id=row["id"],
            sdk_session_id=row["sdk_session_id"],
            project_name=row["project_name"],
            title=row["title"] or "",
            status=row["status"],
            transcript_path=row["transcript_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    sdk_session_id TEXT,
                    project_name TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    status TEXT DEFAULT 'idle',
                    transcript_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_project
                ON sessions (project_name, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_status
                ON sessions (status)
                """
            )

    def create(self, project_name: str, title: str = "") -> SessionMeta:
        session_id = uuid.uuid4().hex
        now = self._now()
        transcript_path = f"transcripts/{session_id}.json"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, project_name, title, status, transcript_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, project_name, title, "idle", transcript_path, now, now),
            )
        session = self.get(session_id)
        if session is None:
            raise RuntimeError("failed to create session")
        return session

    def get(self, session_id: str) -> Optional[SessionMeta]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, sdk_session_id, project_name, title, status, transcript_path, created_at, updated_at
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return self._to_session(row)

    def list(
        self,
        project_name: Optional[str] = None,
        status: Optional[SessionStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionMeta]:
        clauses: list[str] = []
        params: list[object] = []

        if project_name:
            clauses.append("project_name = ?")
            params.append(project_name)
        if status:
            clauses.append("status = ?")
            params.append(status)

        query = "SELECT id, sdk_session_id, project_name, title, status, transcript_path, created_at, updated_at FROM sessions"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([max(1, limit), max(0, offset)])

        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._to_session(row) for row in rows]

    def update_status(self, session_id: str, status: SessionStatus) -> bool:
        now = self._now()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, session_id),
            )
        return cursor.rowcount > 0

    def update_sdk_session_id(self, session_id: str, sdk_session_id: str) -> bool:
        now = self._now()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET sdk_session_id = ?, updated_at = ? WHERE id = ?",
                (sdk_session_id, now, session_id),
            )
        return cursor.rowcount > 0

    def update_title(self, session_id: str, title: str) -> bool:
        now = self._now()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title.strip(), now, session_id),
            )
        return cursor.rowcount > 0

    def delete(self, session_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cursor.rowcount > 0
```

**Step 2: Commit**

```bash
git add webui/server/agent_runtime/session_store.py
git commit -m "refactor(session_store): rewrite as SessionMetaStore for metadata only"
```

---

## Task 4: 创建 transcript_reader.py

**Files:**
- Create: `webui/server/agent_runtime/transcript_reader.py`

**Step 1: 创建 TranscriptReader**

```python
"""
Read SDK transcript files.
"""

import json
from pathlib import Path
from typing import Any


class TranscriptReader:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.transcripts_dir = self.data_dir / "transcripts"

    def read_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Read transcript and return SDK messages as-is."""
        transcript_path = self.transcripts_dir / f"{session_id}.json"
        if not transcript_path.exists():
            return []
        try:
            with open(transcript_path, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("messages", [])
        except (json.JSONDecodeError, OSError):
            return []

    def get_transcript_path(self, session_id: str) -> Path:
        """Get the full path to a transcript file."""
        return self.transcripts_dir / f"{session_id}.json"

    def exists(self, session_id: str) -> bool:
        """Check if transcript exists."""
        return self.get_transcript_path(session_id).exists()
```

**Step 2: Commit**

```bash
git add webui/server/agent_runtime/transcript_reader.py
git commit -m "feat(transcript_reader): add TranscriptReader for SDK transcripts"
```

---

## Task 5: 创建 session_manager.py - 核心组件

**Files:**
- Create: `webui/server/agent_runtime/session_manager.py`

**Step 1: 创建 ManagedSession 和 SessionManager**

```python
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
        self.transcript_reader = TranscriptReader(data_dir)
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
        if hasattr(message, "model_dump"):
            return message.model_dump()
        if hasattr(message, "__dict__"):
            return {k: v for k, v in message.__dict__.items() if not k.startswith("_")}
        return {"raw": str(message)}

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
```

**Step 2: Commit**

```bash
git add webui/server/agent_runtime/session_manager.py
git commit -m "feat(session_manager): add SessionManager with ClaudeSDKClient lifecycle"
```

---

## Task 6: 重写 service.py

**Files:**
- Modify: `webui/server/agent_runtime/service.py`

**Step 1: 重写 AssistantService 使用 SessionManager**

```python
"""
Assistant service orchestration using ClaudeSDKClient.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from lib.project_manager import ProjectManager
from webui.server.agent_runtime.models import SessionMeta, SessionStatus
from webui.server.agent_runtime.session_manager import SessionManager, SDK_AVAILABLE
from webui.server.agent_runtime.session_store import SessionMetaStore
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
        self.transcript_reader = TranscriptReader(self.data_dir)
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
            if managed.consumer_task and not managed.consumer_task.done():
                managed.consumer_task.cancel()
            try:
                await managed.client.disconnect()
            except Exception:
                pass
            del self.session_manager.sessions[session_id]

        return self.meta_store.delete(session_id)

    # ==================== Messages ====================

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        """List messages from transcript."""
        if self.meta_store.get(session_id) is None:
            raise FileNotFoundError(f"session not found: {session_id}")
        return self.transcript_reader.read_messages(session_id)

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

    # ==================== Streaming ====================

    async def stream_events(self, session_id: str) -> AsyncIterator[str]:
        """Stream SSE events for a session."""
        meta = self.meta_store.get(session_id)
        if meta is None:
            raise FileNotFoundError(f"session not found: {session_id}")

        # Check if session is completed - return empty stream
        status = self.session_manager.get_status(session_id)
        if status in ("completed", "error"):
            yield self._sse_event("status", {"status": status})
            return

        # Subscribe to live messages
        queue = await self.session_manager.subscribe(session_id)
        try:
            while True:
                try:
                    message = await asyncio.wait_for(
                        queue.get(),
                        timeout=self.stream_heartbeat_seconds
                    )
                    yield self._sse_event("message", message)

                    # Check for completion
                    msg_type = message.get("type", "")
                    if msg_type == "result":
                        break
                except asyncio.TimeoutError:
                    yield self._sse_event("ping", {"ts": asyncio.get_event_loop().time()})
        except asyncio.CancelledError:
            raise
        finally:
            await self.session_manager.unsubscribe(session_id, queue)

    @staticmethod
    def _sse_event(event: str, data: dict[str, Any]) -> str:
        """Format SSE event."""
        json_data = json.dumps(data, ensure_ascii=False)
        return f"event: {event}\ndata: {json_data}\n\n"

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
```

**Step 2: Commit**

```bash
git add webui/server/agent_runtime/service.py
git commit -m "refactor(service): rewrite to use SessionManager and ClaudeSDKClient"
```

---

## Task 7: 重写 routers/assistant.py

**Files:**
- Modify: `webui/server/routers/assistant.py`

**Step 1: 重写路由使用新 API 结构**

```python
"""
Assistant session APIs.
"""

from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from webui.server.agent_runtime.service import AssistantService

router = APIRouter()

project_root = Path(__file__).parent.parent.parent.parent
assistant_service = AssistantService(project_root=project_root)


class CreateSessionRequest(BaseModel):
    project_name: str = Field(min_length=1)
    title: Optional[str] = ""


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)


class UpdateSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.post("/sessions")
async def create_session(req: CreateSessionRequest):
    try:
        session = await assistant_service.create_session(req.project_name, req.title or "")
        return {"id": session.id, "status": session.status, "created_at": session.created_at}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{req.project_name}' 不存在")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions")
async def list_sessions(
    project_name: Optional[str] = None,
    status: Optional[Literal["idle", "running", "completed", "error", "interrupted"]] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    try:
        sessions = assistant_service.list_sessions(
            project_name=project_name, status=status, limit=limit, offset=offset
        )
        return {"sessions": [s.model_dump() for s in sessions]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    try:
        session = assistant_service.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
        return session.model_dump()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/sessions/{session_id}")
async def update_session(session_id: str, req: UpdateSessionRequest):
    try:
        session = assistant_service.update_session_title(session_id, req.title)
        if session is None:
            raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
        return {"success": True, "session": session.model_dump()}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    try:
        deleted = await assistant_service.delete_session(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{session_id}/messages")
async def list_messages(session_id: str):
    try:
        messages = assistant_service.list_messages(session_id)
        return {"messages": messages}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, req: SendMessageRequest):
    try:
        result = await assistant_service.send_message(session_id, req.content)
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{session_id}/stream")
async def stream_events(session_id: str):
    try:
        session = assistant_service.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")

        return StreamingResponse(
            assistant_service.stream_events(session_id),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/skills")
async def list_skills(project_name: Optional[str] = None):
    try:
        skills = assistant_service.list_available_skills(project_name=project_name)
        return {"skills": skills}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
```

**Step 2: 更新路由注册**

确认 `webui/server/main.py` 中路由前缀为 `/api/v1/assistant`:

```python
# 确保路由使用正确前缀
app.include_router(assistant.router, prefix="/api/v1/assistant", tags=["assistant"])
```

**Step 3: Commit**

```bash
git add webui/server/routers/assistant.py
git commit -m "refactor(router): rewrite assistant API with simplified endpoints"
```

---

## Task 8: 更新前端 api.js

**Files:**
- Modify: `frontend/src/api.js`

**Step 1: 更新 Assistant API 方法**

替换 `// ==================== 助手会话 API ====================` 部分：

```javascript
    // ==================== 助手会话 API ====================

    static async createAssistantSession(projectName, title = '') {
        return this.request('/assistant/sessions', {
            method: 'POST',
            body: JSON.stringify({ project_name: projectName, title }),
        });
    }

    static async listAssistantSessions(projectName = null, status = null) {
        const params = new URLSearchParams();
        if (projectName) params.append('project_name', projectName);
        if (status) params.append('status', status);
        const query = params.toString();
        return this.request(`/assistant/sessions${query ? '?' + query : ''}`);
    }

    static async getAssistantSession(sessionId) {
        return this.request(`/assistant/sessions/${encodeURIComponent(sessionId)}`);
    }

    static async listAssistantMessages(sessionId) {
        return this.request(`/assistant/sessions/${encodeURIComponent(sessionId)}/messages`);
    }

    static async sendAssistantMessage(sessionId, content) {
        return this.request(`/assistant/sessions/${encodeURIComponent(sessionId)}/messages`, {
            method: 'POST',
            body: JSON.stringify({ content }),
        });
    }

    static getAssistantStreamUrl(sessionId) {
        return `${API_BASE}/assistant/sessions/${encodeURIComponent(sessionId)}/stream`;
    }

    static async listAssistantSkills(projectName = null) {
        const params = new URLSearchParams();
        if (projectName) params.append('project_name', projectName);
        const query = params.toString();
        return this.request(`/assistant/skills${query ? '?' + query : ''}`);
    }

    static async updateAssistantSession(sessionId, updates) {
        return this.request(`/assistant/sessions/${encodeURIComponent(sessionId)}`, {
            method: 'PATCH',
            body: JSON.stringify(updates),
        });
    }

    static async deleteAssistantSession(sessionId) {
        return this.request(`/assistant/sessions/${encodeURIComponent(sessionId)}`, {
            method: 'DELETE',
        });
    }
```

**Step 2: 移除旧方法**

删除以下旧方法（如果存在）：
- `archiveAssistantSession`
- `startAssistantMessageStream`

**Step 3: Commit**

```bash
git add frontend/src/api.js
git commit -m "refactor(api): update assistant API to match new backend structure"
```

---

## Task 9: 重写前端 use-assistant-state.js

**Files:**
- Modify: `frontend/src/react/hooks/use-assistant-state.js`

**Step 1: 重写状态管理逻辑**

```javascript
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ROUTE_KIND } from "../constants.js";

export function useAssistantState({
    initialProjectName,
    routeKind,
    currentProjectName,
    projects,
    pushToast,
}) {
    const [assistantPanelOpen, setAssistantPanelOpen] = useState(false);
    const [assistantScopeProject, setAssistantScopeProject] = useState(initialProjectName || "");
    const [assistantSessions, setAssistantSessions] = useState([]);
    const [assistantLoadingSessions, setAssistantLoadingSessions] = useState(false);
    const [assistantCurrentSessionId, setAssistantCurrentSessionId] = useState("");
    const [assistantMessages, setAssistantMessages] = useState([]);
    const [assistantMessagesLoading, setAssistantMessagesLoading] = useState(false);
    const [assistantInput, setAssistantInput] = useState("");
    const [assistantSending, setAssistantSending] = useState(false);
    const [assistantStreamingMessage, setAssistantStreamingMessage] = useState(null);
    const [assistantError, setAssistantError] = useState("");
    const [assistantSkills, setAssistantSkills] = useState([]);
    const [assistantSkillsLoading, setAssistantSkillsLoading] = useState(false);
    const [assistantRefreshToken, setAssistantRefreshToken] = useState(0);
    const [sessionStatus, setSessionStatus] = useState("idle");
    const [sessionDialogOpen, setSessionDialogOpen] = useState(false);
    const [sessionDialogMode, setSessionDialogMode] = useState("create");
    const [sessionDialogTitle, setSessionDialogTitle] = useState("");
    const [sessionDialogSessionId, setSessionDialogSessionId] = useState("");
    const [sessionDialogSubmitting, setSessionDialogSubmitting] = useState(false);
    const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
    const [deleteDialogSessionId, setDeleteDialogSessionId] = useState("");
    const [deleteDialogSessionTitle, setDeleteDialogSessionTitle] = useState("");
    const [deleteDialogSubmitting, setDeleteDialogSubmitting] = useState(false);

    const assistantStreamRef = useRef(null);
    const assistantChatScrollRef = useRef(null);
    const reconnectTimeoutRef = useRef(null);

    const assistantActive = assistantPanelOpen || routeKind === ROUTE_KIND.ASSISTANT;
    const currentAssistantProject = assistantScopeProject || currentProjectName || "";

    // Composed messages: historical + streaming
    const assistantComposedMessages = useMemo(() => {
        const base = Array.isArray(assistantMessages) ? [...assistantMessages] : [];
        if (assistantStreamingMessage) {
            base.push(assistantStreamingMessage);
        }
        return base;
    }, [assistantMessages, assistantStreamingMessage]);

    // Project scope handling
    useEffect(() => {
        if (projects.length === 0) {
            setAssistantScopeProject("");
            return;
        }
        setAssistantScopeProject((prev) => prev || projects[0].name);
    }, [projects]);

    useEffect(() => {
        if (currentProjectName && assistantPanelOpen) {
            setAssistantScopeProject(currentProjectName);
        }
    }, [assistantPanelOpen, currentProjectName]);

    useEffect(() => {
        if (routeKind === ROUTE_KIND.ASSISTANT && assistantPanelOpen) {
            setAssistantPanelOpen(false);
        }
    }, [assistantPanelOpen, routeKind]);

    // Close stream helper
    const closeActiveStream = useCallback(() => {
        if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current);
            reconnectTimeoutRef.current = null;
        }
        if (assistantStreamRef.current) {
            assistantStreamRef.current.close();
            assistantStreamRef.current = null;
        }
    }, []);

    useEffect(() => () => closeActiveStream(), [closeActiveStream]);

    // Load sessions
    const loadAssistantSessions = useCallback(async () => {
        if (!assistantActive) return;
        setAssistantLoadingSessions(true);
        try {
            const data = await window.API.listAssistantSessions(currentAssistantProject || null);
            const sessions = data.sessions || [];
            setAssistantSessions(sessions);
            setAssistantCurrentSessionId((prev) => {
                if (prev && sessions.some((s) => s.id === prev)) return prev;
                return sessions[0]?.id || "";
            });
        } catch (error) {
            pushToast(`加载会话失败：${error.message}`, "error");
        } finally {
            setAssistantLoadingSessions(false);
        }
    }, [assistantActive, currentAssistantProject, pushToast]);

    useEffect(() => {
        void loadAssistantSessions();
    }, [loadAssistantSessions, assistantRefreshToken]);

    // Load skills
    const loadAssistantSkills = useCallback(async () => {
        if (!assistantActive) return;
        setAssistantSkillsLoading(true);
        try {
            const data = await window.API.listAssistantSkills(currentAssistantProject || null);
            setAssistantSkills(data.skills || []);
        } catch (error) {
            pushToast(`加载技能列表失败：${error.message}`, "error");
            setAssistantSkills([]);
        } finally {
            setAssistantSkillsLoading(false);
        }
    }, [assistantActive, currentAssistantProject, pushToast]);

    useEffect(() => {
        void loadAssistantSkills();
    }, [loadAssistantSkills]);

    // Connect to SSE stream
    const connectStream = useCallback((sessionId) => {
        closeActiveStream();

        const streamUrl = window.API.getAssistantStreamUrl(sessionId);
        const source = new EventSource(streamUrl);
        assistantStreamRef.current = source;

        source.addEventListener("message", (event) => {
            try {
                const message = JSON.parse(event.data);
                setAssistantMessages((prev) => [...prev, message]);

                // Check for result message
                if (message.type === "result") {
                    setSessionStatus(message.subtype === "success" ? "completed" : "error");
                    setAssistantStreamingMessage(null);
                    setAssistantSending(false);
                    closeActiveStream();
                }
            } catch (err) {
                console.error("Failed to parse SSE message:", err);
            }
        });

        source.addEventListener("status", (event) => {
            try {
                const data = JSON.parse(event.data);
                setSessionStatus(data.status);
                if (data.status === "completed" || data.status === "error") {
                    closeActiveStream();
                }
            } catch (err) {
                console.error("Failed to parse status event:", err);
            }
        });

        source.addEventListener("ping", () => {
            // Heartbeat, no action needed
        });

        source.onerror = () => {
            // Reconnect after 3 seconds if session is running
            if (sessionStatus === "running") {
                reconnectTimeoutRef.current = setTimeout(() => {
                    connectStream(sessionId);
                }, 3000);
            }
        };
    }, [closeActiveStream, sessionStatus]);

    // Load messages or connect stream based on status
    const loadOrConnectSession = useCallback(async (sessionId) => {
        if (!sessionId) {
            setAssistantMessages([]);
            setSessionStatus("idle");
            return;
        }

        setAssistantMessagesLoading(true);
        setAssistantStreamingMessage(null);
        setAssistantError("");

        try {
            // Get session status
            const session = await window.API.getAssistantSession(sessionId);
            setSessionStatus(session.status);

            if (session.status === "running") {
                // Connect to stream for live updates
                connectStream(sessionId);
            } else {
                // Load history from transcript
                const data = await window.API.listAssistantMessages(sessionId);
                setAssistantMessages(data.messages || []);
            }
        } catch (error) {
            pushToast(`加载消息失败：${error.message}`, "error");
        } finally {
            setAssistantMessagesLoading(false);
        }
    }, [connectStream, pushToast]);

    useEffect(() => {
        if (!assistantActive) return;
        void loadOrConnectSession(assistantCurrentSessionId);
    }, [assistantActive, assistantCurrentSessionId, loadOrConnectSession]);

    // Auto scroll
    useEffect(() => {
        if (assistantChatScrollRef.current) {
            assistantChatScrollRef.current.scrollTop = assistantChatScrollRef.current.scrollHeight;
        }
    }, [assistantComposedMessages, assistantCurrentSessionId, assistantMessagesLoading]);

    // Ensure session exists
    const ensureAssistantSession = useCallback(async () => {
        if (assistantCurrentSessionId) return assistantCurrentSessionId;

        const projectName = currentAssistantProject || projects[0]?.name;
        if (!projectName) throw new Error("请先创建至少一个项目");

        const data = await window.API.createAssistantSession(projectName, "");
        setAssistantSessions((prev) => [{ id: data.id, ...data }, ...prev]);
        setAssistantCurrentSessionId(data.id);
        return data.id;
    }, [assistantCurrentSessionId, currentAssistantProject, projects]);

    // Send message
    const handleSendAssistantMessage = useCallback(async (event) => {
        event.preventDefault();

        const content = assistantInput.trim();
        if (!content || assistantSending) return;

        setAssistantSending(true);
        setAssistantError("");
        setAssistantInput("");
        setAssistantStreamingMessage(null);

        try {
            const sessionId = await ensureAssistantSession();

            // Add optimistic user message
            setAssistantMessages((prev) => [
                ...prev,
                { type: "user", content, id: `tmp-${Date.now()}` },
            ]);

            // Send and connect to stream
            await window.API.sendAssistantMessage(sessionId, content);
            setSessionStatus("running");
            connectStream(sessionId);
        } catch (error) {
            setAssistantError(error.message || "发送失败");
            setAssistantSending(false);
        }
    }, [assistantInput, assistantSending, connectStream, ensureAssistantSession]);

    // Session dialog handlers
    const handleCreateSession = useCallback(() => {
        const projectName = currentAssistantProject || projects[0]?.name;
        if (!projectName) {
            pushToast("请先创建项目", "error");
            return;
        }
        setSessionDialogMode("create");
        setSessionDialogSessionId("");
        setSessionDialogTitle("");
        setSessionDialogOpen(true);
    }, [currentAssistantProject, projects, pushToast]);

    const handleRenameSession = useCallback((session) => {
        if (!session?.id) return;
        setSessionDialogMode("rename");
        setSessionDialogSessionId(session.id);
        setSessionDialogTitle(session.title || "");
        setSessionDialogOpen(true);
    }, []);

    const closeSessionDialog = useCallback(() => {
        if (sessionDialogSubmitting) return;
        setSessionDialogOpen(false);
        setSessionDialogMode("create");
        setSessionDialogTitle("");
        setSessionDialogSessionId("");
    }, [sessionDialogSubmitting]);

    const submitSessionDialog = useCallback(async (event) => {
        event.preventDefault();
        if (sessionDialogSubmitting) return;

        setSessionDialogSubmitting(true);
        try {
            if (sessionDialogMode === "create") {
                const projectName = currentAssistantProject || projects[0]?.name;
                if (!projectName) {
                    pushToast("请先创建项目", "error");
                    return;
                }
                const data = await window.API.createAssistantSession(projectName, sessionDialogTitle.trim());
                setAssistantCurrentSessionId(data.id);
                setAssistantRefreshToken((prev) => prev + 1);
                pushToast("已创建新会话", "success");
            } else {
                const normalized = sessionDialogTitle.trim();
                if (!normalized) {
                    pushToast("标题不能为空", "error");
                    return;
                }
                if (!sessionDialogSessionId) {
                    pushToast("未找到会话", "error");
                    return;
                }
                await window.API.updateAssistantSession(sessionDialogSessionId, { title: normalized });
                setAssistantRefreshToken((prev) => prev + 1);
                pushToast("会话已重命名", "success");
            }
            setSessionDialogOpen(false);
            setSessionDialogMode("create");
            setSessionDialogTitle("");
            setSessionDialogSessionId("");
        } catch (error) {
            pushToast(`保存会话失败：${error.message}`, "error");
        } finally {
            setSessionDialogSubmitting(false);
        }
    }, [currentAssistantProject, projects, pushToast, sessionDialogMode, sessionDialogSessionId, sessionDialogSubmitting, sessionDialogTitle]);

    // Delete dialog handlers
    const handleDeleteSession = useCallback((session) => {
        if (!session?.id) return;
        setDeleteDialogSessionId(session.id);
        setDeleteDialogSessionTitle(session.title || "");
        setDeleteDialogOpen(true);
    }, []);

    const closeDeleteDialog = useCallback(() => {
        if (deleteDialogSubmitting) return;
        setDeleteDialogOpen(false);
        setDeleteDialogSessionId("");
        setDeleteDialogSessionTitle("");
    }, [deleteDialogSubmitting]);

    const confirmDeleteSession = useCallback(async (event) => {
        event.preventDefault();
        if (deleteDialogSubmitting) return;
        if (!deleteDialogSessionId) {
            pushToast("未找到会话", "error");
            return;
        }

        setDeleteDialogSubmitting(true);
        try {
            await window.API.deleteAssistantSession(deleteDialogSessionId);
            if (assistantCurrentSessionId === deleteDialogSessionId) {
                setAssistantCurrentSessionId("");
                setAssistantMessages([]);
            }
            setAssistantRefreshToken((prev) => prev + 1);
            pushToast("会话已删除", "success");
            setDeleteDialogOpen(false);
            setDeleteDialogSessionId("");
            setDeleteDialogSessionTitle("");
        } catch (error) {
            pushToast(`删除失败：${error.message}`, "error");
        } finally {
            setDeleteDialogSubmitting(false);
        }
    }, [assistantCurrentSessionId, deleteDialogSessionId, deleteDialogSubmitting, pushToast]);

    const handleAssistantScopeChange = useCallback((projectName) => {
        setAssistantScopeProject(projectName);
        setAssistantCurrentSessionId("");
        setAssistantRefreshToken((prev) => prev + 1);
    }, []);

    const toggleAssistantPanel = useCallback(() => {
        if (!assistantPanelOpen && currentProjectName) {
            setAssistantScopeProject(currentProjectName);
        }
        setAssistantPanelOpen((prev) => !prev);
    }, [assistantPanelOpen, currentProjectName]);

    return {
        assistantPanelOpen,
        setAssistantPanelOpen,
        assistantSessions,
        assistantLoadingSessions,
        assistantCurrentSessionId,
        setAssistantCurrentSessionId,
        assistantMessagesLoading,
        assistantInput,
        setAssistantInput,
        assistantSending,
        assistantError,
        assistantSkills,
        assistantSkillsLoading,
        assistantComposedMessages,
        currentAssistantProject,
        sessionStatus,
        sessionDialogOpen,
        sessionDialogMode,
        sessionDialogTitle,
        setSessionDialogTitle,
        sessionDialogSubmitting,
        deleteDialogOpen,
        deleteDialogSessionTitle,
        deleteDialogSubmitting,
        handleSendAssistantMessage,
        handleCreateSession,
        handleRenameSession,
        handleDeleteSession,
        closeSessionDialog,
        submitSessionDialog,
        closeDeleteDialog,
        confirmDeleteSession,
        handleAssistantScopeChange,
        toggleAssistantPanel,
        assistantChatScrollRef,
    };
}
```

**Step 2: Commit**

```bash
git add frontend/src/react/hooks/use-assistant-state.js
git commit -m "refactor(use-assistant-state): rewrite for ClaudeSDKClient with SSE reconnection"
```

---

## Task 10: 更新前端 ChatMessage 组件

**Files:**
- Modify: `frontend/src/react/components/chat/ChatMessage.js`

**Step 1: 适配 SDK 消息格式**

更新组件以处理 SDK 原生消息格式：

```javascript
// 在组件中添加消息类型判断
const getMessageType = (message) => {
    // SDK 消息使用 type 字段
    if (message.type) return message.type;
    // 兼容旧格式
    if (message.role) return message.role;
    return "unknown";
};

const getMessageContent = (message) => {
    // SDK AssistantMessage 的 content 是数组
    if (Array.isArray(message.content)) {
        return message.content;
    }
    // 字符串内容
    if (typeof message.content === "string") {
        return [{ type: "text", text: message.content }];
    }
    return [];
};
```

**Step 2: Commit**

```bash
git add frontend/src/react/components/chat/ChatMessage.js
git commit -m "refactor(ChatMessage): adapt to SDK message format"
```

---

## Task 11: 删除旧文件和清理

**Files:**
- Delete: `webui/server/agent_runtime/streaming.py` (如果不再需要)

**Step 1: 检查并删除不再使用的文件**

```bash
# 检查 streaming.py 是否还有其他引用
grep -r "from.*streaming import" webui/server/
```

如果没有引用，删除：

```bash
git rm webui/server/agent_runtime/streaming.py
```

**Step 2: 更新 __init__.py（如果存在）**

**Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove deprecated streaming module"
```

---

## Task 12: 集成测试

**Step 1: 运行后端测试**

```bash
cd /Users/pollochen/Documents/ArcReel/.worktrees/sdk-client-migration
python -m pytest tests/ -v
```

**Step 2: 启动服务并手动测试**

```bash
# 终端 1: 启动后端
cd webui && python -m uvicorn server.main:app --reload --port 8000

# 终端 2: 启动前端
cd frontend && pnpm dev
```

**Step 3: 测试场景**

1. 创建新会话
2. 发送消息，验证 SSE 流式响应
3. 刷新页面，验证历史消息加载
4. 在会话进行中刷新页面，验证断线重连
5. 测试 interrupted 状态恢复

**Step 4: Commit 测试通过**

```bash
git add -A
git commit -m "test: verify ClaudeSDKClient migration works end-to-end"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | 创建数据目录结构 | `.agent_data/`, `.gitignore` |
| 2 | 重写 models.py | `models.py` |
| 3 | 重写 session_store.py | `session_store.py` |
| 4 | 创建 transcript_reader.py | `transcript_reader.py` |
| 5 | 创建 session_manager.py | `session_manager.py` |
| 6 | 重写 service.py | `service.py` |
| 7 | 重写 router | `assistant.py` |
| 8 | 更新前端 API | `api.js` |
| 9 | 重写状态管理 | `use-assistant-state.js` |
| 10 | 更新 ChatMessage | `ChatMessage.js` |
| 11 | 清理旧文件 | - |
| 12 | 集成测试 | - |
