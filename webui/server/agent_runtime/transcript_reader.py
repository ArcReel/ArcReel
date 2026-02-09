"""
Read SDK transcript files (JSONL format).

History rendering always returns grouped conversation turns.
The grouping rules are shared with live SSE streaming in turn_grouper.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from webui.server.agent_runtime.turn_grouper import group_messages_into_turns


class TranscriptReader:
    """Read messages from Claude SDK transcript files."""

    MESSAGE_TYPES = {"user", "assistant", "result"}

    def __init__(self, data_dir: Path, project_root: Optional[Path] = None):
        self.data_dir = Path(data_dir)
        self.project_root = Path(project_root) if project_root else None
        self._claude_projects_dir = Path.home() / ".claude" / "projects"

    def _resolve_project_root(self, project_name: Optional[str] = None) -> Optional[Path]:
        """Resolve the project root used by Claude SDK transcript encoding."""
        if project_name and self.project_root:
            return self.project_root / "projects" / project_name
        return self.project_root

    def _get_sdk_transcript_path(
        self,
        sdk_session_id: str,
        project_name: Optional[str] = None,
    ) -> Optional[Path]:
        """Get the path to an SDK transcript file."""
        session_project_root = self._resolve_project_root(project_name)
        if not session_project_root:
            return None
        encoded_path = str(session_project_root).replace("/", "-").replace(".", "-")
        project_dir = self._claude_projects_dir / encoded_path
        transcript_path = project_dir / f"{sdk_session_id}.jsonl"
        return transcript_path if transcript_path.exists() else None

    def read_messages(
        self,
        session_id: str,
        sdk_session_id: Optional[str] = None,
        project_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Read transcript and return grouped conversation turns."""
        raw_messages = self.read_raw_messages(
            session_id,
            sdk_session_id,
            project_name=project_name,
        )
        if raw_messages:
            return group_messages_into_turns(raw_messages)

        legacy_path = self.data_dir / "transcripts" / f"{session_id}.json"
        if legacy_path.exists():
            # Legacy files were already persisted in display-ready format.
            return self._read_json_transcript(legacy_path)

        return []

    def read_raw_messages(
        self,
        session_id: str,
        sdk_session_id: Optional[str] = None,
        project_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Read raw transcript messages (user/assistant/result) without grouping.

        This is used by SSE streaming to build a live turn snapshot that matches
        history grouping logic.
        """
        if sdk_session_id:
            transcript_path = self._get_sdk_transcript_path(
                sdk_session_id,
                project_name=project_name,
            )
            if transcript_path:
                return self._read_jsonl_transcript_raw(transcript_path)
        # Legacy JSON transcript files are display-ready and not raw streams.
        return []

    def _read_jsonl_transcript_raw(self, path: Path) -> list[dict[str, Any]]:
        """Read SDK JSONL transcript file and extract raw messages."""
        messages: list[dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg = self._parse_jsonl_entry(entry)
                    if msg:
                        messages.append(msg)
        except OSError:
            pass
        return messages

    def _parse_jsonl_entry(self, entry: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Parse a single JSONL entry into a raw message dict."""
        msg_type = entry.get("type")
        if msg_type not in self.MESSAGE_TYPES:
            return None

        if msg_type == "user":
            message = entry.get("message", {})
            return {
                "type": "user",
                "content": message.get("content", ""),
                "uuid": entry.get("uuid"),
                "timestamp": entry.get("timestamp"),
            }
        if msg_type == "assistant":
            message = entry.get("message", {})
            return {
                "type": "assistant",
                "content": message.get("content", []),
                "uuid": entry.get("uuid"),
                "timestamp": entry.get("timestamp"),
            }
        if msg_type == "result":
            return {
                "type": "result",
                "subtype": entry.get("subtype", ""),
                "session_id": entry.get("sessionId"),
                "uuid": entry.get("uuid"),
                "timestamp": entry.get("timestamp"),
            }
        return None

    def _read_json_transcript(self, path: Path) -> list[dict[str, Any]]:
        """Read legacy JSON transcript file."""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("messages", [])
        except (json.JSONDecodeError, OSError):
            return []

    def get_transcript_path(self, session_id: str) -> Path:
        """Get the full path to a transcript file (legacy)."""
        return self.data_dir / "transcripts" / f"{session_id}.json"

    def exists(
        self,
        session_id: str,
        sdk_session_id: Optional[str] = None,
        project_name: Optional[str] = None,
    ) -> bool:
        """Check if transcript exists."""
        if sdk_session_id:
            sdk_path = self._get_sdk_transcript_path(
                sdk_session_id,
                project_name=project_name,
            )
            if sdk_path and sdk_path.exists():
                return True
        return self.get_transcript_path(session_id).exists()
