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
