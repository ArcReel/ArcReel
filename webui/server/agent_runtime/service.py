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
        meta = self.meta_store.get(session_id)
        if meta is None:
            raise FileNotFoundError(f"session not found: {session_id}")
        return self.transcript_reader.read_messages(session_id, meta.sdk_session_id)

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
