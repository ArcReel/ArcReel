"""
Read SDK transcript files (JSONL format).

Handles message grouping to create conversation "turns" where:
- Consecutive assistant messages are merged into one turn
- tool_use blocks are paired with their corresponding tool_result
- Skill invocations group: tool_use + tool_result + skill_content
"""

import json
from pathlib import Path
from typing import Any, Optional

# Constants for skill content detection
_SKILL_BASE_DIR_PREFIX = "Base directory for this skill:"
_SKILL_CONTENT_PREFIX = "Skill content:"
_SKILL_PATH_MARKER = ".claude/skills/"
_SKILL_FILE_MARKER = "SKILL.md"


def _is_skill_content_text(text: str) -> bool:
    """Check if text is system-injected skill content."""
    return (
        text.startswith(_SKILL_BASE_DIR_PREFIX) or
        text.startswith(_SKILL_CONTENT_PREFIX) or
        (_SKILL_PATH_MARKER in text and _SKILL_FILE_MARKER in text)
    )


class TranscriptReader:
    """Read messages from Claude SDK transcript files."""

    MESSAGE_TYPES = {"user", "assistant", "result"}

    def __init__(self, data_dir: Path, project_root: Optional[Path] = None):
        self.data_dir = Path(data_dir)
        self.project_root = Path(project_root) if project_root else None
        self._claude_projects_dir = Path.home() / ".claude" / "projects"

    def _get_sdk_transcript_path(self, sdk_session_id: str) -> Optional[Path]:
        """Get the path to an SDK transcript file."""
        if not self.project_root:
            return None
        encoded_path = str(self.project_root).replace("/", "-").replace(".", "-")
        project_dir = self._claude_projects_dir / encoded_path
        transcript_path = project_dir / f"{sdk_session_id}.jsonl"
        return transcript_path if transcript_path.exists() else None

    def read_messages(
        self, session_id: str, sdk_session_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Read transcript and return messages grouped into conversation turns."""
        if sdk_session_id:
            transcript_path = self._get_sdk_transcript_path(sdk_session_id)
            if transcript_path:
                raw_messages = self._read_jsonl_transcript_raw(transcript_path)
                return self._group_into_turns(raw_messages)

        legacy_path = self.data_dir / "transcripts" / f"{session_id}.json"
        if legacy_path.exists():
            return self._read_json_transcript(legacy_path)

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
        """Parse a single JSONL entry into a message dict."""
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
        elif msg_type == "assistant":
            message = entry.get("message", {})
            return {
                "type": "assistant",
                "content": message.get("content", []),
                "uuid": entry.get("uuid"),
                "timestamp": entry.get("timestamp"),
            }
        elif msg_type == "result":
            return {
                "type": "result",
                "subtype": entry.get("subtype", ""),
                "session_id": entry.get("sessionId"),
                "uuid": entry.get("uuid"),
                "timestamp": entry.get("timestamp"),
            }
        return None

    def _is_system_injected_user_message(self, content: Any) -> bool:
        """Check if a user message is system-injected (tool_result or skill content)."""
        if isinstance(content, str):
            return _is_skill_content_text(content.strip())

        if isinstance(content, list):
            return self._all_blocks_are_system_injected(content)

        return False

    def _all_blocks_are_system_injected(self, blocks: list[Any]) -> bool:
        """Check if all blocks in a list are system-injected."""
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "tool_result":
                continue
            if block_type == "text":
                text = block.get("text", "").strip()
                if _is_skill_content_text(text):
                    continue
                return False
            return False
        return True

    def _group_into_turns(self, raw_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Group raw messages into conversation turns."""
        if not raw_messages:
            return []

        turns: list[dict[str, Any]] = []
        current_turn: Optional[dict[str, Any]] = None
        tool_use_map: dict[str, int] = {}

        for msg in raw_messages:
            msg_type = msg.get("type")

            if msg_type == "result":
                current_turn = self._handle_result_message(msg, current_turn, turns)
            elif msg_type == "user":
                current_turn = self._handle_user_message(msg, current_turn, turns, tool_use_map)
            elif msg_type == "assistant":
                current_turn = self._handle_assistant_message(msg, current_turn, turns, tool_use_map)

        if current_turn:
            turns.append(current_turn)

        return turns

    def _handle_result_message(
        self,
        msg: dict[str, Any],
        current_turn: Optional[dict[str, Any]],
        turns: list[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """Handle a result message - flush current turn and add result."""
        if current_turn:
            turns.append(current_turn)
        turns.append({
            "type": "result",
            "subtype": msg.get("subtype", ""),
            "uuid": msg.get("uuid"),
            "timestamp": msg.get("timestamp"),
        })
        return None

    def _handle_user_message(
        self,
        msg: dict[str, Any],
        current_turn: Optional[dict[str, Any]],
        turns: list[dict[str, Any]],
        tool_use_map: dict[str, int],
    ) -> Optional[dict[str, Any]]:
        """Handle a user message - either system-injected or real user input."""
        content = msg.get("content", "")

        if self._is_system_injected_user_message(content):
            if current_turn and current_turn.get("type") == "assistant":
                self._attach_system_content_to_turn(current_turn, content, tool_use_map)
                return current_turn
            elif not current_turn:
                return {
                    "type": "system",
                    "content": self._normalize_content(content),
                    "uuid": msg.get("uuid"),
                    "timestamp": msg.get("timestamp"),
                }
            return current_turn

        # Real user message
        if current_turn:
            turns.append(current_turn)
        return {
            "type": "user",
            "content": self._normalize_content(content),
            "uuid": msg.get("uuid"),
            "timestamp": msg.get("timestamp"),
        }

    def _handle_assistant_message(
        self,
        msg: dict[str, Any],
        current_turn: Optional[dict[str, Any]],
        turns: list[dict[str, Any]],
        tool_use_map: dict[str, int],
    ) -> dict[str, Any]:
        """Handle an assistant message - merge or start new turn."""
        content = msg.get("content", [])
        new_blocks = self._normalize_content(content)

        if current_turn and current_turn.get("type") == "assistant":
            existing = current_turn.get("content", [])
            self._track_tool_uses(new_blocks, existing, tool_use_map)
            existing.extend(new_blocks)
            return current_turn

        if current_turn:
            turns.append(current_turn)

        self._track_tool_uses(new_blocks, [], tool_use_map)
        return {
            "type": "assistant",
            "content": new_blocks,
            "uuid": msg.get("uuid"),
            "timestamp": msg.get("timestamp"),
        }

    def _track_tool_uses(
        self,
        new_blocks: list[dict[str, Any]],
        existing_blocks: list[dict[str, Any]],
        tool_use_map: dict[str, int],
    ) -> None:
        """Track tool_use block IDs for later pairing with tool_result."""
        offset = len(existing_blocks)
        for i, block in enumerate(new_blocks):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_id = block.get("id")
                if tool_id:
                    tool_use_map[tool_id] = offset + i

    def _normalize_content(self, content: Any) -> list[dict[str, Any]]:
        """Normalize content to a list of content blocks."""
        if isinstance(content, str):
            if not content.strip():
                return []
            return [{"type": "text", "text": content}]
        if isinstance(content, list):
            return list(content)
        return []

    def _attach_system_content_to_turn(
        self,
        turn: dict[str, Any],
        content: Any,
        tool_use_map: dict[str, int],
    ) -> None:
        """Attach system-injected content to assistant turn."""
        blocks = self._normalize_content(content)
        turn_content = turn.get("content", [])

        for block in blocks:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type", "")

            if block_type == "tool_result":
                self._attach_tool_result(block, turn_content, tool_use_map)
            elif block_type == "text":
                self._attach_text_block(block, turn_content)
            else:
                turn_content.append(block)

    def _attach_tool_result(
        self,
        block: dict[str, Any],
        turn_content: list[dict[str, Any]],
        tool_use_map: dict[str, int],
    ) -> None:
        """Attach tool_result to its corresponding tool_use block."""
        tool_use_id = block.get("tool_use_id")
        if tool_use_id and tool_use_id in tool_use_map:
            for existing_block in turn_content:
                if (
                    isinstance(existing_block, dict) and
                    existing_block.get("type") == "tool_use" and
                    existing_block.get("id") == tool_use_id
                ):
                    existing_block["result"] = block.get("content", "")
                    existing_block["is_error"] = block.get("is_error", False)
                    return
        turn_content.append(block)

    def _attach_text_block(
        self,
        block: dict[str, Any],
        turn_content: list[dict[str, Any]],
    ) -> None:
        """Attach text block - handle skill content specially."""
        text = block.get("text", "").strip()
        if _is_skill_content_text(text):
            for existing_block in reversed(turn_content):
                if (
                    isinstance(existing_block, dict) and
                    existing_block.get("type") == "tool_use" and
                    existing_block.get("name") == "Skill"
                ):
                    existing_block["skill_content"] = text
                    return
            turn_content.append({"type": "skill_content", "text": text})
        else:
            turn_content.append(block)

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
