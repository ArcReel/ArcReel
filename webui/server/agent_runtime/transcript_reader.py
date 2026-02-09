"""
Read SDK transcript files (JSONL format).
"""

import json
import re
from pathlib import Path
from typing import Any, Optional


class TranscriptReader:
    """Read messages from Claude SDK transcript files."""

    # Message types that represent actual conversation messages
    MESSAGE_TYPES = {"user", "assistant", "result"}

    def __init__(self, data_dir: Path, project_root: Optional[Path] = None):
        self.data_dir = Path(data_dir)
        self.project_root = Path(project_root) if project_root else None
        self._claude_projects_dir = Path.home() / ".claude" / "projects"

    def _get_sdk_transcript_path(self, sdk_session_id: str) -> Optional[Path]:
        """Get the path to an SDK transcript file."""
        if not self.project_root:
            return None

        # SDK encodes the project path:
        # 1. Replace / with -
        # 2. Replace . with -
        encoded_path = str(self.project_root).replace("/", "-").replace(".", "-")
        project_dir = self._claude_projects_dir / encoded_path
        transcript_path = project_dir / f"{sdk_session_id}.jsonl"

        if transcript_path.exists():
            return transcript_path
        return None

    def read_messages(
        self, session_id: str, sdk_session_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """
        Read transcript and return messages.

        Args:
            session_id: Our internal session ID (for legacy compatibility)
            sdk_session_id: The SDK's session ID for finding transcript file

        Returns:
            List of message dictionaries
        """
        # Try SDK transcript first
        if sdk_session_id:
            transcript_path = self._get_sdk_transcript_path(sdk_session_id)
            if transcript_path:
                return self._read_jsonl_transcript(transcript_path)

        # Fall back to legacy location
        legacy_path = self.data_dir / "transcripts" / f"{session_id}.json"
        if legacy_path.exists():
            return self._read_json_transcript(legacy_path)

        return []

    def _read_jsonl_transcript(self, path: Path) -> list[dict[str, Any]]:
        """Read SDK JSONL transcript file and extract messages."""
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

                    msg_type = entry.get("type")
                    if msg_type not in self.MESSAGE_TYPES:
                        continue

                    # Extract the message based on type
                    if msg_type == "user":
                        message = entry.get("message", {})
                        messages.append({
                            "type": "user",
                            "content": message.get("content", ""),
                            "uuid": entry.get("uuid"),
                            "timestamp": entry.get("timestamp"),
                        })
                    elif msg_type == "assistant":
                        message = entry.get("message", {})
                        messages.append({
                            "type": "assistant",
                            "content": message.get("content", []),
                            "uuid": entry.get("uuid"),
                            "timestamp": entry.get("timestamp"),
                        })
                    elif msg_type == "result":
                        messages.append({
                            "type": "result",
                            "subtype": entry.get("subtype", ""),
                            "session_id": entry.get("sessionId"),
                            "uuid": entry.get("uuid"),
                            "timestamp": entry.get("timestamp"),
                        })

        except OSError:
            pass
        return messages

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

    def exists(self, session_id: str, sdk_session_id: Optional[str] = None) -> bool:
        """Check if transcript exists."""
        if sdk_session_id:
            sdk_path = self._get_sdk_transcript_path(sdk_session_id)
            if sdk_path and sdk_path.exists():
                return True
        return self.get_transcript_path(session_id).exists()
