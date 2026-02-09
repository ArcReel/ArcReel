"""Unit tests for TranscriptReader."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from webui.server.agent_runtime.transcript_reader import TranscriptReader


class TestTranscriptReader(unittest.TestCase):
    def test_read_jsonl_transcript(self):
        """Test reading SDK JSONL transcript file."""
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            project_root = tmppath / "project"
            project_root.mkdir()

            # Create mock SDK transcript location
            encoded_path = str(project_root).replace("/", "-")
            claude_dir = tmppath / ".claude" / "projects" / encoded_path
            claude_dir.mkdir(parents=True)

            sdk_session_id = "test-sdk-session-123"
            transcript_file = claude_dir / f"{sdk_session_id}.jsonl"

            # Write mock transcript entries
            entries = [
                {
                    "type": "queue-operation",
                    "operation": "dequeue",
                    "timestamp": "2026-02-09T08:00:00Z",
                },
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Hello, Claude!"},
                    "uuid": "user-123",
                    "timestamp": "2026-02-09T08:00:01Z",
                },
                {
                    "type": "progress",
                    "data": {"type": "hook_progress"},
                    "timestamp": "2026-02-09T08:00:02Z",
                },
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Hello! How can I help you?"}
                        ],
                    },
                    "uuid": "assistant-456",
                    "timestamp": "2026-02-09T08:00:03Z",
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "sessionId": sdk_session_id,
                    "uuid": "result-789",
                    "timestamp": "2026-02-09T08:00:04Z",
                },
            ]

            with open(transcript_file, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")

            # Create reader with custom claude projects dir
            reader = TranscriptReader(tmppath, project_root=project_root)
            reader._claude_projects_dir = tmppath / ".claude" / "projects"

            # Read messages
            messages = reader.read_messages("internal-id", sdk_session_id)

            self.assertEqual(len(messages), 3)  # user, assistant, result

            # Check user message
            self.assertEqual(messages[0]["type"], "user")
            self.assertEqual(messages[0]["content"], "Hello, Claude!")
            self.assertEqual(messages[0]["uuid"], "user-123")

            # Check assistant message
            self.assertEqual(messages[1]["type"], "assistant")
            self.assertEqual(len(messages[1]["content"]), 1)
            self.assertEqual(messages[1]["content"][0]["type"], "text")
            self.assertEqual(messages[1]["content"][0]["text"], "Hello! How can I help you?")

            # Check result message
            self.assertEqual(messages[2]["type"], "result")
            self.assertEqual(messages[2]["subtype"], "success")

    def test_read_legacy_json_transcript(self):
        """Test reading legacy JSON transcript file."""
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            transcripts_dir = tmppath / "transcripts"
            transcripts_dir.mkdir()

            session_id = "legacy-session-123"
            transcript_file = transcripts_dir / f"{session_id}.json"

            # Write mock legacy transcript
            legacy_data = {
                "messages": [
                    {"type": "user", "content": "Hello"},
                    {"type": "assistant", "content": "Hi there!"},
                ]
            }
            with open(transcript_file, "w", encoding="utf-8") as f:
                json.dump(legacy_data, f)

            reader = TranscriptReader(tmppath)
            messages = reader.read_messages(session_id)

            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["content"], "Hello")
            self.assertEqual(messages[1]["content"], "Hi there!")

    def test_read_empty_returns_empty_list(self):
        """Test that reading non-existent transcript returns empty list."""
        with TemporaryDirectory() as tmpdir:
            reader = TranscriptReader(Path(tmpdir))
            messages = reader.read_messages("nonexistent")
            self.assertEqual(messages, [])

    def test_exists_with_sdk_session(self):
        """Test exists() method with SDK session ID."""
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            project_root = tmppath / "project"
            project_root.mkdir()

            # Create mock SDK transcript
            encoded_path = str(project_root).replace("/", "-")
            claude_dir = tmppath / ".claude" / "projects" / encoded_path
            claude_dir.mkdir(parents=True)

            sdk_session_id = "sdk-123"
            transcript_file = claude_dir / f"{sdk_session_id}.jsonl"
            transcript_file.write_text("{}\n")

            reader = TranscriptReader(tmppath, project_root=project_root)
            reader._claude_projects_dir = tmppath / ".claude" / "projects"

            self.assertTrue(reader.exists("internal-id", sdk_session_id))
            self.assertFalse(reader.exists("internal-id", "nonexistent"))


if __name__ == "__main__":
    unittest.main()
